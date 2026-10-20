"""Where MPCCD panel images come from online: the real detector (olpy) or test data.

Both sources have the same interface:
    newest_tag()      newest tag available
    read(tag)         list of panel images (float32) of that tag, None if the tag is gone
    infos()           read_det_info(0) of every panel (for the geometry)
    det_ids           panel IDs
    run               run number of the last shot read (None if unknown)

TestSource replays the panel images saved by tests/diag_mpccd_geometry.py with a
60 Hz tag clock: pedestal + noise, and the saved pattern made of single photons
of a given energy (Poisson counts per pixel, some charge shared with a neighbour),
converted to ADU with the panels' mp_absgain like the real calibration. So the
viewer's keV histogram shows photon peaks. It uses no SACLA library, so it also
runs on a laptop in smoke mode.
"""
import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from silas.core import bootstrap

DEFAULT_TEST_DATA = os.path.join(bootstrap.PACKAGE_ROOT, "output", "diag", "mpccd_panels.npz")


class OlpySource(object):
    """Live panels from the online storage (DAQ-USER-LAN, or the olpy simulator)."""

    def __init__(self, det_ids: Sequence[str]) -> None:
        import olpy

        self.det_ids = list(det_ids)
        self.readers = [olpy.StorageReader(d) for d in self.det_ids]
        self.buffers = [olpy.StorageBuffer(r) for r in self.readers]
        self._infos = None  # type: Optional[List[Dict[str, Any]]]
        self.run = None  # type: Optional[int]
        self._n_read = 0

    def newest_tag(self) -> int:
        import olpy

        return int(self.readers[0].collect(self.buffers[0], olpy.NEWESTTAG))

    def read(self, tag: int) -> Optional[List[np.ndarray]]:
        """All panels of ``tag`` (None if any panel no longer has it)."""
        import olpy

        images = []
        for reader, buf in zip(self.readers, self.buffers):
            try:
                reader.collect(buf, tag)
            except olpy.APIError:
                return None
            images.append(buf.read_det_data(0))
        if self._infos is None:
            self._infos = [dict(b.read_det_info(0)) for b in self.buffers]
        if self._n_read % 30 == 0:  # the run number (for the photon energy from the run record)
            try:
                self.run = int(self.buffers[0].read_det_info(0)["run_number"])
            except (KeyError, TypeError, ValueError, olpy.APIError):
                pass
        self._n_read += 1
        return images

    def infos(self) -> List[Dict[str, Any]]:
        if self._infos is None:
            tag = self.newest_tag()
            if self.read(tag) is None:  # fills _infos
                self._infos = [dict(b.read_det_info(0)) for b in self.buffers]
        return list(self._infos or [])


class TestSource(object):
    """Replays saved panels as a fake 30 Hz detector (no SACLA library needed)."""

    def __init__(self, path: str = DEFAULT_TEST_DATA, rate_hz: float = 60.0, tag_step: int = 1,
                 dark_level: float = 100.0, photon_keV: float = 12.0,
                 ev_per_absgain_adu: float = 3.65, photons_per_pixel: float = 0.01) -> None:
        if not os.path.exists(path):
            raise FileNotFoundError("test data {} not found: run tests/diag_mpccd_geometry.py once "
                                    "with the local SACLA environment (sacla_local_api)".format(path))
        with np.load(path) as d:
            self.base = d["images"].astype(np.float32)
            self.det_ids = [str(x) for x in d["det_ids"]]
            self._infos = json.loads(str(d["info_json"]))
        self.rate_hz = rate_hz
        self.tag_step = tag_step
        self.t0 = time.time()
        self.tag0 = 1000000
        self.dark_level = dark_level
        rng = np.random.RandomState(0)
        self.pedestal = (dark_level + 5.0 * rng.standard_normal(self.base.shape)).astype(np.float32)
        self.signal = np.clip(self.base - np.median(self.base, axis=(1, 2), keepdims=True), 0, None)
        self.run = int(self._infos[0].get("run_number", 0)) if self._infos else None
        # a few noise and photon patterns, cycled per shot (cheaper than new random numbers every shot)
        self.noise = [(10.0 * rng.standard_normal(self.base.shape)).astype(np.float32) for _ in range(4)]
        self.photon_keV = float(photon_keV)
        g = np.array([float(i.get("mp_absgain", 1.0)) for i in self._infos], np.float64)
        self.adu_per_photon = self.photon_keV * 1000.0 / (g * float(ev_per_absgain_adu))
        self.photons = [self._photon_pattern(rng, photons_per_pixel) for _ in range(6)]
        self.xrays_on = True
        """False: images are dark (pedestal + noise only), e.g. to test capture_dark()."""

    def _photon_pattern(self, rng: Any, per_pixel: float) -> np.ndarray:
        """One shot of photons following the saved pattern, in ADU of each panel."""
        lam = self.signal / max(float(self.signal.mean()), 1e-6) * per_pixel
        counts = rng.poisson(np.minimum(lam, 50.0)).astype(np.float32)
        out = counts * self.adu_per_photon.reshape(-1, 1, 1).astype(np.float32)
        # charge sharing: for 40 % of the hit pixels a fraction (up to half) goes to a neighbour
        p, r, c = np.nonzero(counts)
        share = rng.random_sample(len(p)) < 0.4
        p, r, c = p[share], r[share], c[share]
        frac = (0.5 * rng.random_sample(len(p))).astype(np.float32)
        dr, dc = [np.array(v)[rng.randint(0, 4, len(p))] for v in ((1, -1, 0, 0), (0, 0, 1, -1))]
        r2 = np.clip(r + dr, 0, out.shape[1] - 1)
        c2 = np.clip(c + dc, 0, out.shape[2] - 1)
        moved = out[p, r, c] * frac
        out[p, r, c] -= moved
        np.add.at(out, (p, r2, c2), moved)
        return out

    def newest_tag(self) -> int:
        return self.tag0 + self.tag_step * int((time.time() - self.t0) * self.rate_hz)

    def read(self, tag: int) -> Optional[List[np.ndarray]]:
        if tag > self.newest_tag():
            return None
        k = (tag // self.tag_step) % 997
        x = self.pedestal + self.noise[k % len(self.noise)]
        if self.xrays_on:
            x += self.photons[(k * 7) % len(self.photons)]
        return list(x)

    def infos(self) -> List[Dict[str, Any]]:
        return list(self._infos)


class RunSource(object):
    """Replays a STORED run with stpy (HPC): its shots in order at ``rate_hz``, over and over.
    For looking at old data in the viewer, e.g. to set ROIs or try corrections offline: after a
    change of dark or threshold the viewer starts again with new shots, so there must always be
    new ones.

    newest_tag() counts on past the end of the run (pass 2 continues after the last tag,
    keeping the tag step), so the readers see ever newer tags; read() and real_tag() map
    them back to the run's tags."""

    def __init__(self, det_ids: Sequence[str], bl: int, run: int, rate_hz: float = 10.0) -> None:
        import stpy

        from silas.core import db

        self.det_ids = list(det_ids)
        self.run = int(run)
        self.rate_hz = float(rate_hz)
        print("replay: reading the tag list of run {} (dbpy) ...".format(self.run), flush=True)
        self.tags = sorted(db.tags(bl, self.run))
        if not self.tags:
            raise ValueError("run {} has no tags".format(run))
        step = self.tags[1] - self.tags[0] if len(self.tags) > 1 else 1
        self.span = self.tags[-1] - self.tags[0] + step      # tags per pass, incl. the step to the next pass
        print("replay: {} tags; opening stpy readers for {} ...".format(len(self.tags), ", ".join(self.det_ids)),
              flush=True)
        self.readers = [stpy.StorageReader(d, bl, (self.run,)) for d in self.det_ids]
        self.buffers = [stpy.StorageBuffer(r) for r in self.readers]
        self.t0 = time.time()
        self._infos = None  # type: Optional[List[Dict[str, Any]]]

    def newest_tag(self) -> int:
        i = int((time.time() - self.t0) * self.rate_hz)
        n = len(self.tags)
        return int(self.tags[i % n]) + (i // n) * self.span

    def real_tag(self, tag: int) -> int:
        """The run's tag behind a tag of a later pass."""
        return self.tags[0] + (int(tag) - self.tags[0]) % self.span

    def read(self, tag: int) -> Optional[List[np.ndarray]]:
        import stpy

        tag = self.real_tag(tag)
        images = []
        for reader, buf in zip(self.readers, self.buffers):
            try:
                reader.collect(buf, int(tag))
            except stpy.APIError:
                return None
            images.append(buf.read_det_data(0))
        if self._infos is None:
            self._infos = [dict(b.read_det_info(0)) for b in self.buffers]
        return images

    def infos(self) -> List[Dict[str, Any]]:
        if self._infos is None:
            print("replay: reading the first shot (detector info) ...", flush=True)
        if self._infos is None and self.read(self.tags[0]) is None:
            raise ValueError("run {}: the first shot cannot be read for {}".format(self.run, self.det_ids))
        return list(self._infos or [])


def open_source(det_ids: Sequence[str], test: bool, test_data: Optional[str] = None,
                tag_step: int = 1, photon_keV: Optional[float] = None,
                ev_per_absgain_adu: float = 3.65, run: Optional[int] = None, bl: int = 3,
                rate_hz: float = 10.0) -> Any:
    """OlpySource, TestSource (test photons: photon_keV, default 12 keV) or RunSource (a stored
    run replayed with stpy, run=...)."""
    if run is not None:
        return RunSource(det_ids, bl, run, rate_hz)
    if test:
        src = TestSource(test_data or DEFAULT_TEST_DATA, tag_step=tag_step, photon_keV=photon_keV or 12.0,
                         ev_per_absgain_adu=ev_per_absgain_adu)
        if list(det_ids) != src.det_ids:
            print("test data: panels {} (config: {})".format(", ".join(src.det_ids), ", ".join(det_ids)))
        return src
    return OlpySource(det_ids)
