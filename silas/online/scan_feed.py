"""Where the online scan viewer gets scans, shot flags and panels from.

LiveFeed  the beamline: scans from the run logger's files (runlog.csv, scans.csv), shot
          flags (X-ray shutter, beam status, pump shutter) from the SyncDB (dbpy), panels
          from the online storage (olpy)
TestFeed  no SACLA library: a simulated sequence of scans on a 60 Hz tag clock, with a
          pump pattern, X-ray-off shots, a signal that changes with the step, and an online
          buffer that only keeps the newest tags (to test catching up)

Both have the same interface:

    det_ids, infos()             the MPCCD panels (geometry, gain)
    newest_tag()                 newest tag in the online storage
    scan(which)                  the scan to follow: {"scan_id", "motor", "status", "runs": [...]},
                                 runs: {"run", "step", "value", "start_tag", "end_tag" (None while
                                 running), "status", "taghi"}; None if there is no scan yet
    tags(run_info, upto)         the run's tags up to ``upto`` (ascending)
    flags(run_info, tags)        per tag: (use?, pump state): use = X-rays on and beam OK;
                                 pump state 0 = on, 1 = off, -1 = unknown, 0 for all if no pump
    read(tag, panels)            {panel index: image} or GONE (no longer in the buffer) or None
    photon_energy(run)           keV or None
    n_states                     2 with a pump detector, else 1
"""
import math
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from silas.core import energy, names, scanroi
from silas.core.config import Config

GONE = "gone"
"""read(): the tag is no longer in the online buffer (older tags are gone too)."""

XRAY_SHUTTER = "laser_shutter"           # XFEL shutter: 0 = X-rays off
pump_settings = scanroi.pump_settings


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


# ====================================================================== live
class LiveFeed(object):
    """Beamline: run logger files + dbpy + olpy."""

    def __init__(self, cfg: Config, det_ids: Sequence[str], tag_step: int = 1) -> None:
        self.cfg = cfg
        self.det_ids = list(det_ids)
        self.tag_step = int(tag_step)
        self.pump, self.pump_on = pump_settings(cfg)
        self.n_states = 2 if self.pump else 1
        self.xray = names.resolve(cfg, XRAY_SHUTTER)
        self._readers = None  # type: Optional[List[Any]]
        self._buffers = None  # type: Optional[List[Any]]
        self._infos = None  # type: Optional[List[Dict[str, Any]]]
        self._taglists = {}  # type: Dict[int, List[int]]

    # ------------------------------------------------------------ detector
    def _open(self) -> None:
        if self._readers is None:
            import olpy

            self._readers = [olpy.StorageReader(d) for d in self.det_ids]
            self._buffers = [olpy.StorageBuffer(r) for r in self._readers]

    def newest_tag(self) -> int:
        import olpy

        self._open()
        assert self._readers is not None and self._buffers is not None
        return int(self._readers[0].collect(self._buffers[0], olpy.NEWESTTAG))

    def infos(self) -> List[Dict[str, Any]]:
        import olpy

        if self._infos is None:
            self._open()
            assert self._readers is not None and self._buffers is not None
            for r, b in zip(self._readers, self._buffers):
                r.collect(b, olpy.NEWESTTAG)
            self._infos = [dict(b.read_det_info(0)) for b in self._buffers]
        return list(self._infos)

    def read(self, tag: int, panels: Sequence[int]) -> Any:
        import olpy

        self._open()
        assert self._readers is not None and self._buffers is not None
        gone = getattr(olpy, "OL_ERR_TAGDATAGONE", -200010000)
        out = {}
        for k in panels:
            try:
                self._readers[k].collect(self._buffers[k], int(tag))
            except olpy.APIError as err:
                return GONE if err.args and err.args[0] == gone else None
            out[k] = self._buffers[k].read_det_data(0)
        return out

    # ------------------------------------------------------------ scans (run logger files)
    def scan(self, which: Optional[int] = None) -> Optional[Dict[str, Any]]:
        from silas.core import scan

        try:
            return scan.scan_runs(self.cfg, -1 if which is None else which)
        except (LookupError, IndexError):
            return None

    def tags(self, info: Dict[str, Any], upto: int) -> List[int]:
        run = int(info["run"])
        if info.get("end_tag") is not None:            # finished: the real tag list, once
            if run not in self._taglists:
                from silas.core import db

                try:
                    self._taglists[run] = sorted(db.tags(self.cfg.beamline, run))
                except Exception:  # noqa: BLE001  not readable (yet): from the tag range
                    start, end = info.get("start_tag"), info["end_tag"]
                    return list(range(int(start), int(end) + 1, self.tag_step)) if start else []
            return [t for t in self._taglists[run] if t <= upto]
        start = info.get("start_tag")
        if not start:
            return []
        return list(range(int(start), int(upto) + 1, self.tag_step))

    def flags(self, info: Dict[str, Any], tags: Sequence[int]) -> Tuple[np.ndarray, np.ndarray]:
        import dbpy

        from silas.core import db

        n = len(tags)
        taghi = int(info.get("taghi") or db.high_tag(self.cfg.beamline, int(info["run"])))
        use = np.ones(n, bool)
        if self.xray:
            x = np.array(db.read_point(self.xray, taghi, tags, errors="nan"))
            use &= ~(x == 0)                            # NaN (not recorded): keep
        try:
            beam = np.array(dbpy.read_beamstatuslist(self.cfg.beamline, taghi, tuple(int(t) for t in tags)))
            use &= beam == 1
        except dbpy.APIError:
            pass
        if not self.pump:
            return use, np.zeros(n, np.int64)
        p = np.array(db.read_point(self.pump, taghi, tags, errors="nan"))
        state = np.where(np.isnan(p), -1, np.where(p == self.pump_on, 0, 1)).astype(np.int64)
        return use, state

    def photon_energy(self, run: int) -> Optional[float]:
        return energy.photon_energy(self.cfg, run)


# ====================================================================== test
class TestFeed(object):
    """Simulated scans (no SACLA library): see the module docstring.

    Scan k (k = 0, 1, ...): ``n_steps`` runs of ``run_len`` shots with ``gap`` tags between
    runs, then one plain run, then the next scan. Step s is at delay 1100 + 34 s. Pump off
    for every 4th shot, X-rays off for every 50th. The photon signal is multiplied by 1 for
    pump off and by 1 - 0.4 (1 - exp(-(s - 2) / 2)) after time zero (step 2) for pump on.
    The online buffer holds the newest ``buffer`` tags (older: GONE)."""

    def __init__(self, cfg: Config, tag_step: int = 1, rate_hz: float = 60.0, n_steps: int = 8,
                 run_len: int = 150, gap: int = 30, buffer: int = 400, path: Optional[str] = None,
                 t0: Optional[float] = None, photon_keV: Optional[float] = None) -> None:
        from silas.online.mpccd_source import DEFAULT_TEST_DATA, TestSource

        self.cfg = cfg
        e = photon_keV or energy.photon_energy_override(cfg) or 12.0
        self.src = TestSource(path or DEFAULT_TEST_DATA, rate_hz=rate_hz, tag_step=tag_step, photon_keV=e)
        if t0 is not None:
            self.src.t0 = t0
        self.photon_keV = e
        self.det_ids = self.src.det_ids
        self.tag_step = int(tag_step)
        self.n_steps, self.run_len, self.gap, self.buffer = n_steps, run_len, gap, buffer
        self.pump, self.pump_on = pump_settings(cfg)
        self.n_states = 2 if self.pump else 1
        self.tag0 = self.src.tag0
        self.per_scan = n_steps + 1                    # n_steps scan runs + 1 plain run
        self.now_tag = None  # type: Optional[int]
        """Set to fix the clock (tests): newest_tag() returns it."""
        self.run0 = 2000000

    # ------------------------------------------------------------ clock / layout
    def newest_tag(self) -> int:
        if self.now_tag is not None:
            return int(self.now_tag)
        return self.src.newest_tag()

    def _layout(self, k: int) -> Tuple[int, int]:
        """(first, last) tag of run index k."""
        first = self.tag0 + k * (self.run_len + self.gap) * self.tag_step
        return first, first + (self.run_len - 1) * self.tag_step

    def _run_at(self, tag: int) -> Optional[int]:
        k = (tag - self.tag0) // ((self.run_len + self.gap) * self.tag_step)
        first, last = self._layout(int(k))
        return int(k) if first <= tag <= last else None

    def step_of(self, k: int) -> Optional[int]:
        s = k % self.per_scan
        return s + 1 if s < self.n_steps else None     # steps 1..n, None: the plain run

    def factor(self, step: Optional[int], pump_on: bool) -> float:
        if step is None or not pump_on:
            return 1.0
        x = (step - 1) - 2.0
        return 1.0 - 0.4 * (1.0 - math.exp(-x / 2.0)) if x > 0 else 1.0

    # ------------------------------------------------------------ interface
    def infos(self) -> List[Dict[str, Any]]:
        return self.src.infos()

    def scan(self, which: Optional[int] = None) -> Optional[Dict[str, Any]]:
        newest = self.newest_tag()
        # the newest run that has started; a scan = its n_steps runs + the plain run after it
        k_now = int((newest - self.tag0) // ((self.run_len + self.gap) * self.tag_step))
        scan_idx = k_now // self.per_scan
        if which is not None and which < 0:
            scan_idx += which + 1
        if scan_idx < 0:
            return None
        runs = []
        for s in range(self.n_steps):
            k = scan_idx * self.per_scan + s
            first, last = self._layout(k)
            if first > newest:
                break
            done = last <= newest
            runs.append({"run": self.run0 + k, "step": float(s + 1), "value": 1100.0 + 34.0 * s,
                         "start_tag": float(first), "end_tag": float(last) if done else None,
                         "status": 0.0 if done else 2.0, "taghi": 202610.0})
        status = "complete" if len(runs) == self.n_steps and runs[-1]["end_tag"] else "running"
        return {"scan_id": self.run0 + scan_idx * self.per_scan, "motor": "delay_m (test)",
                "status": status, "runs": runs}

    def tags(self, info: Dict[str, Any], upto: int) -> List[int]:
        first = int(info["start_tag"])
        last = int(info["end_tag"]) if info.get("end_tag") is not None else int(upto)
        return list(range(first, min(last, int(upto)) + 1, self.tag_step))

    def flags(self, info: Dict[str, Any], tags: Sequence[int]) -> Tuple[np.ndarray, np.ndarray]:
        i = np.asarray(tags, np.int64) // self.tag_step
        use = (i % 50) != 7
        if not self.pump:
            return use, np.zeros(len(tags), np.int64)
        return use, np.where(i % 4 == 0, 1, 0).astype(np.int64)

    def read(self, tag: int, panels: Sequence[int]) -> Any:
        newest = self.newest_tag()
        if tag > newest:
            return None
        if tag < newest - self.buffer * self.tag_step:
            return GONE
        k = self._run_at(tag)
        i = tag // self.tag_step
        pump_on = bool(self.pump) and i % 4 != 0
        f = self.factor(self.step_of(k) if k is not None else None, pump_on)
        n = self.src.noise[i % len(self.src.noise)]
        ph = self.src.photons[(i * 7) % len(self.src.photons)]
        return {kk: self.src.pedestal[kk] + n[kk] + f * ph[kk] for kk in panels}

    def photon_energy(self, run: int) -> Optional[float]:
        return self.photon_keV


def open_feed(cfg: Config, det_ids: Sequence[str], test: bool, tag_step: int = 1, **kwargs: Any) -> Any:
    """LiveFeed (beamline) or TestFeed (no SACLA)."""
    if test:
        return TestFeed(cfg, tag_step=tag_step, **kwargs)
    return LiveFeed(cfg, det_ids, tag_step)
