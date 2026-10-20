"""Where time-tool camera images and shot flags come from.

LiveCamera   the beamline: images from the online storage (olpy), flags of a tag from the
             SyncDB (dbpy; they arrive there a few seconds after the image)
StoredRun    a stored run (HPC): images with stpy, the flags of all its tags read at once
TestCamera   no SACLA library: a made-up camera on a 30 Hz tag clock (tag step 2), the probe
             spectrum with an edge that wanders from shot to shot when X-rays and laser are
             on; blocks of shots without X-rays (background shots), every 10th shot without
             laser, now and then a bad beam status

The same interface for all:
    det_id, label, shape (rows, cols; after the first image), run (None if unknown)
    tags             list of the run's tags (StoredRun), None (live)
    newest_tag()     newest tag in the buffer (live)
    read(tag)        image (2-D float32) or None (not readable / no longer in the buffer)
    flags(tag)       (xray, laser, beam_ok): xray 0 off / 1 on / NaN unknown, laser 1 on / 0
                     off / NaN unknown, beam_ok True / False
"""
import math
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from silas.core import timetool

Flags = Tuple[float, float, bool]
UNKNOWN = (math.nan, math.nan, False)  # type: Flags


def detect_tag_step(src: Any, timeout_s: float = 3.0) -> int:
    """Tag difference between consecutive shots, from the newest tag over a moment
    (2 at 30 Hz, 1 at 60 Hz). 1 if it cannot be seen."""
    seen = []  # type: List[int]
    t_end = time.time() + timeout_s
    while time.time() < t_end and len(seen) < 6:
        try:
            tag = int(src.newest_tag())
        except Exception:  # noqa: BLE001  e.g. olpy not ready
            tag = -1
        if tag > 0 and (not seen or tag != seen[-1]):
            seen.append(tag)
        time.sleep(0.004)
    diffs = [b - a for a, b in zip(seen, seen[1:]) if b > a]
    return max(1, min(diffs)) if diffs else 1


# ====================================================================== live
class LiveCamera(object):
    """Online: olpy images, SyncDB flags (dbpy) of the running run."""

    def __init__(self, cfg: Any, det_id: Optional[str] = None) -> None:
        import olpy

        self.cfg = cfg
        self.det_id = det_id or timetool.camera(cfg, list(olpy.read_detidlist()))
        self.reader = olpy.StorageReader(self.det_id)
        self.buf = olpy.StorageBuffer(self.reader)
        self.label = "{} live (olpy)".format(self.det_id)
        self.tags = None  # type: Optional[List[int]]
        self.run = None  # type: Optional[int]
        self.shape = None  # type: Optional[Tuple[int, ...]]
        self._n = 0
        self._taghi = {}  # type: Dict[int, int]
        self._flags = {}  # type: Dict[int, Flags]
        self._tried = {}  # type: Dict[int, float]
        self.laser, self.laser_on = timetool.laser_settings(cfg)
        self.xray = self._resolve(timetool.XRAY_SHUTTER)

    def _resolve(self, name: str) -> Optional[str]:
        from silas.core import names

        try:
            return names.resolve(self.cfg, name)
        except (KeyError, ValueError):
            return None

    def newest_tag(self) -> int:
        import olpy

        return int(self.reader.collect(self.buf, olpy.NEWESTTAG))

    def read(self, tag: int) -> Optional[np.ndarray]:
        import olpy

        try:
            self.reader.collect(self.buf, int(tag))
            img = self.buf.read_det_data(0)
        except olpy.APIError:
            return None
        if self._n % 30 == 0:       # the run number (for the flags' high tag)
            try:
                info = dict(self.buf.read_det_info(0))  # type: Dict[str, Any]
                self.run = int(info.get("run_number", 0)) or self.run
            except (olpy.APIError, KeyError, TypeError, ValueError):
                pass
        self._n += 1
        self.shape = img.shape
        return img

    def flags(self, tag: int) -> Flags:
        """The tag's flags from the SyncDB; UNKNOWN while they are not there yet (asked again
        at most every 0.5 s)."""
        import dbpy

        from silas.core import db

        tag = int(tag)
        if tag in self._flags:
            return self._flags[tag]
        if time.time() - self._tried.get(tag, 0.0) < 0.5 or self.run is None:
            return UNKNOWN
        self._tried[tag] = time.time()
        if len(self._tried) > 2000:
            self._tried = {}
        try:
            if self.run not in self._taghi:
                self._taghi[self.run] = db.high_tag(self.cfg.beamline, self.run)
            hi = self._taghi[self.run]
            x = float(dbpy.read_syncdatalist_float(self.xray, hi, (tag,))[0]) if self.xray else math.nan
            if self.laser:
                lv = float(dbpy.read_syncdatalist_float(self.laser, hi, (tag,))[0])
                laser = float(timetool.laser_flag([lv], self.laser_on)[0])
            else:
                laser = 1.0               # no laser flag: always on
            beam = int(dbpy.read_beamstatuslist(self.cfg.beamline, hi, (tag,))[0]) == 1
        except dbpy.APIError:
            return UNKNOWN
        out = (x, laser, beam)  # type: Flags
        self._flags[tag] = out
        if len(self._flags) > 5000:
            self._flags = {}
        return out


# ====================================================================== stored run
class StoredRun(object):
    """Offline (HPC): one stored run, read with stpy; all flags read once (dbpy)."""

    def __init__(self, cfg: Any, run: int, det_id: Optional[str] = None) -> None:
        import stpy

        from silas.core import db

        self.cfg = cfg
        self.run = int(run)
        bl = cfg.beamline
        self.det_id = det_id or timetool.camera(cfg, db.run_detectors(bl, self.run))
        print("run {}: reading the tag list and shot flags (dbpy) ...".format(self.run), flush=True)
        f = timetool.run_flags(cfg, self.run)
        self.tags = [int(t) for t in f["tags"]]  # type: Optional[List[int]]
        if not self.tags:
            raise ValueError("run {} has no tags".format(run))
        self.cls = f["cls"]
        self._index = {t: i for i, t in enumerate(self.tags)}
        self._xray, self._laser, self._beam = f["xray"], f["laser"], f["beam_ok"]
        print("run {}: {} tags: {}".format(self.run, len(self.tags), timetool.class_counts(self.cls)))
        self.reader = stpy.StorageReader(self.det_id, bl, (self.run,))
        self.buf = stpy.StorageBuffer(self.reader)
        self.label = "{} run {} (stpy)".format(self.det_id, self.run)
        self.shape = None  # type: Optional[Tuple[int, ...]]

    def newest_tag(self) -> int:
        assert self.tags is not None
        return self.tags[-1]

    def read(self, tag: int) -> Optional[np.ndarray]:
        import stpy

        try:
            self.reader.collect(self.buf, int(tag))
            img = self.buf.read_det_data(0)
        except stpy.APIError:
            return None
        self.shape = img.shape
        return img

    def flags(self, tag: int) -> Flags:
        i = self._index.get(int(tag))
        if i is None:
            return UNKNOWN
        return float(self._xray[i]), float(self._laser[i]), bool(self._beam[i])


# ====================================================================== test
class TestCamera(object):
    """A made-up time-tool camera (see the module docstring). No SACLA library."""

    SHAPE = (478, 1920)
    BUFFER = 600            # tags kept "in the online buffer"
    EPOCH = 1.79e9          # start of the test tag clock (unix time)

    def __init__(self, rate_hz: float = 30.0, tag_step: int = 2) -> None:
        self.det_id = "OPAL-TEST"
        self.label = "TEST camera ({:g} Hz)".format(rate_hz)
        self.tags = None  # type: Optional[List[int]]
        self.run = 1
        self.rate_hz = float(rate_hz)
        self.tag_step = int(tag_step)
        self.t0 = self.EPOCH        # the same clock in every process (MPI readers and the display)
        self.tag0 = 2000000
        ny, nx = self.SHAPE
        self.shape = self.SHAPE  # type: Optional[Tuple[int, ...]]
        y = np.arange(ny, dtype=np.float32)
        self.x = np.arange(nx, dtype=np.float32)
        gx = np.exp(-0.5 * ((self.x - 950.0) / 420.0) ** 2)
        gy = np.exp(-0.5 * ((y - 190.0) / 35.0) ** 2)
        self.probe = (2000.0 * np.outer(gy, gx)).astype(np.float32)
        rng = np.random.RandomState(1)
        self.noise = [(30.0 + 8.0 * rng.standard_normal(self.SHAPE)).astype(np.float32) for _ in range(4)]

    def _k(self, tag: int) -> int:
        return (int(tag) - self.tag0) // self.tag_step

    def newest_tag(self) -> int:
        return self.tag0 + self.tag_step * int((time.time() - self.t0) * self.rate_hz)

    def edge(self, tag: int) -> float:
        """Where the test edge is for this shot (pixels)."""
        k = self._k(tag)
        rng = np.random.RandomState(k % 100000)
        return 1000.0 + 150.0 * math.sin(2 * math.pi * k / 400.0) + 25.0 * float(rng.standard_normal())

    RUN_SHOTS = 1500        # shots per test run (50 s at 30 Hz)
    FIRST_RUN = 1000

    def run_of(self, tag: int) -> int:
        """The test run a tag belongs to."""
        return self.FIRST_RUN + max(self._k(tag), 0) // self.RUN_SHOTS

    def run_stopped(self, run: int) -> bool:
        return self.run_of(self.newest_tag()) > run

    def flags(self, tag: int) -> Flags:
        k = self._k(tag)
        xray = 0.0 if (k // 60) % 5 == 4 else 1.0
        laser = 0.0 if k % 10 == 9 else 1.0
        return xray, laser, k % 97 != 0

    def read(self, tag: int) -> Optional[np.ndarray]:
        newest = self.newest_tag()
        if tag > newest or tag < newest - self.BUFFER * self.tag_step or (tag - self.tag0) % self.tag_step:
            return None
        k = self._k(tag)
        xray, laser, _ = self.flags(tag)
        img = self.noise[k % 4].copy()
        if laser:
            amp = 1.0 + 0.15 * math.sin(k * 0.37)
            if xray:
                t = 0.85 + 0.15 * 0.5 * (1.0 + np.tanh((self.x - self.edge(tag)) / 12.0))   # rising edge
                img += amp * self.probe * t.astype(np.float32)[None, :]
            else:
                img += amp * self.probe
        return img


def open_camera(cfg: Any, run: Optional[int] = None, test: bool = False, det_id: Optional[str] = None) -> Any:
    """StoredRun (run=N), TestCamera (test=True) or LiveCamera."""
    if run is not None:
        return StoredRun(cfg, run, det_id)
    if test:
        return TestCamera()
    return LiveCamera(cfg, det_id)


def shot_flags_text(flags: Flags) -> str:
    """"X-rays on, laser on, beam OK" etc."""
    x, laser, beam = flags
    xs = "?" if x != x else ("off" if x == 0 else "on")
    ls = "?" if laser != laser else ("on" if laser == 1 else "off")
    bs = "OK" if beam else ("?" if (x != x and laser != laser) else "bad")
    return "X-rays {}  laser {}  beam {}".format(xs, ls, bs)


def classify_one(flags: Flags) -> int:
    x, laser, beam = flags
    return int(timetool.classify(np.array([x]), np.array([laser]), np.array([beam]))[0])


def class_matches(cls: int, want: Sequence[int]) -> bool:
    return not want or cls in want
