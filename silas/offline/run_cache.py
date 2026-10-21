"""Offline MPCCD view (HPC, stpy): every shot of stored run(s), loaded once, averaged with the
current corrections.

    start_mpccd_viewer(name="octal", run=1234501)                  # from the session
    start_mpccd_viewer(name="octal", run=[1234501, 1234503], max_tags=500)
    python -m silas.online.mpccd_view --array octal --run 1234501 --workers 8

The viewer window is the online one (silas.online.mpccd_view: same keys, ROIs, geometry
tuning, config); only the data differ. Instead of following the detector it shows the
AVERAGE of all loaded shots, split by the pump state (key w: pump on / pump off).

Loading (once, in parallel over --workers Python processes on this node):
  * the tags of every run (dbpy); every ``every``-th, at most ``max_tags`` per run
  * per shot X-rays on / beam OK and the pump state, as in the scan analysis
    (silas.offline.scan_analysis.shot_flags): shots with X-rays off, bad beam or an
    unknown pump state are not loaded
  * the RAW panels of the remaining shots (stpy) go into a cache file, as 16-bit integers
    when the data are whole numbers (MPCCD ADU), else 32-bit floats:
      - in RAM (/dev/shm) if it fits in half the free memory; deleted when the viewer stops
      - else on /work/<user>/silas_cache (or the config's paths.cache); kept, so opening
        the same runs again with the same options needs no loading
    cache="ram" or cache="work" chooses the place instead.

Every change of dark or threshold (keys d, t, +, -, or the session) recomputes the averages
from the cache with the same processes (seconds for one run), together with the summed
single-shot histograms in keV (every 2nd row and column of every shot). The view keeps the
previous image until the new one is ready ("computing ..." in the status).
"""
import atexit
import getpass
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import db, runner, scanroi
from silas.core.config import Config
from silas.core.corrections import Corrections

STATE_NAMES = ("pump on", "pump off")
HIST_STRIDE = 2                  # histogram: every 2nd row and column of every shot
CHUNK_BYTES = 64 * 1024 ** 2     # float32 shots handled at once by one process
SHM = "/dev/shm"


# ====================================================================== memory and place
def _cgroup_limit() -> Optional[int]:
    """Memory limit of this job (cgroup v2 or v1), None if none is set."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as f:
                text = f.read().strip()
        except (OSError, IOError):
            continue
        if text.isdigit() and int(text) < (1 << 60):
            return int(text)
    return None


def _mem_available() -> Optional[int]:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, IOError, ValueError):
        pass
    return None


def ram_room() -> int:
    """Bytes a cache in RAM may take: half of the smallest of free memory, the job's memory
    limit and the free space of /dev/shm. 0 if there is no /dev/shm."""
    if not os.path.isdir(SHM):
        return 0
    st = os.statvfs(SHM)
    sizes = [st.f_bavail * st.f_frsize]
    for v in (_mem_available(), _cgroup_limit()):
        if v:
            sizes.append(v)
    return int(0.5 * min(sizes))


def work_folder(cfg: Config) -> str:
    """Folder of kept caches: paths.cache, else /work/<user>/silas_cache, else <output>/cache."""
    path = cfg.get("paths.cache", None)
    if not path:
        user_work = os.path.join("/work", getpass.getuser())
        path = os.path.join(user_work, "silas_cache") if os.path.isdir(user_work) \
            else os.path.join(cfg.output_dir(), "cache")
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def clean_ram_caches() -> None:
    """Remove RAM caches of viewers of this user that are no longer running (e.g. killed)."""
    if not os.path.isdir(SHM):
        return
    prefix = "silas_{}_".format(getpass.getuser())
    for name in os.listdir(SHM):
        if not (name.startswith(prefix) and name.endswith(".dat")):
            continue
        try:
            pid = int(name[len(prefix):].split("_")[0])
        except ValueError:
            continue
        if not _pid_alive(pid):
            try:
                os.remove(os.path.join(SHM, name))
                print("removed the RAM cache of a stopped viewer:", name)
            except OSError:
                pass


# ====================================================================== which shots
def plan_shots(cfg: Config, runs: Sequence[int], max_tags: Optional[int], every: int) -> Dict[str, Any]:
    """The shots to load: [(run, tag, state)], with the counts of the dropped ones."""
    from silas.offline.scan_analysis import shot_flags

    bl = cfg.beamline
    items = []  # type: List[Tuple[int, int, int]]
    counts = {"tags": 0, "xray_off": 0, "pump_unknown": 0}
    for run in runs:
        tags = list(db.tags(bl, int(run)))[::max(1, int(every))]
        if max_tags:
            tags = tags[:int(max_tags)]
        if not tags:
            print("run {}: no tags".format(run))
            continue
        taghi = db.high_tag(bl, int(run))
        flags = shot_flags(cfg, bl, int(run), taghi, tags)
        #Check if all shots have unknown xfel shutter state
        if flags["use"].sum() == 0:
            print("run {}: all {} tags have X-FEL off".format(run, len(tags)))
            print("setting all {} tags to X-FEL on".format(len(tags)))
            flags["use"] = np.ones(len(tags), bool)
        counts["tags"] += len(tags)
        for i, tag in enumerate(tags):
            if not flags["use"][i]:
                counts["xray_off"] += 1
            elif flags["state"][i] < 0:
                counts["pump_unknown"] += 1
            else:
                items.append((int(run), int(tag), int(flags["state"][i])))
        print("run {}: {} tags, {} to load".format(run, len(tags), sum(1 for it in items if it[0] == run)),
              flush=True)
    pump, _ = scanroi.pump_settings(cfg)
    return {"items": items, "counts": counts, "pump": pump}


def _storage_dtype(shot: np.ndarray) -> str:
    """uint16 / int16 for whole-number data (MPCCD ADU) in range, else float32."""
    if not np.all(shot == np.rint(shot)):
        return "float32"
    lo, hi = float(shot.min()), float(shot.max())
    if lo >= 0 and hi < 65536:
        return "uint16"
    if lo >= -32768 and hi < 32768:
        return "int16"
    return "float32"


# ====================================================================== worker functions
def _load_part(payload: Tuple[str, Tuple[int, ...], str, int, List[str], List[Tuple[int, int, int]], str]) \
        -> Tuple[List[int], int]:
    """Read shots [(index, run, tag)] into the cache file. Returns (unreadable indices, shots
    that had to be rounded to the integer type)."""
    from silas.offline.scan_analysis import PanelReader

    path, shape, dtype, bl, det_ids, items, label = payload
    mm = np.memmap(path, dtype=dtype, mode="r+", shape=shape)
    reader = PanelReader(bl, det_ids)
    panels = list(range(len(det_ids)))
    integer = np.issubdtype(np.dtype(dtype), np.integer)
    info = np.iinfo(dtype) if integer else None
    bad = []  # type: List[int]
    rounded = 0
    t0 = time.time()
    for j, (i, run, tag) in enumerate(items):
        data = reader.read(run, tag, panels)
        if data is None:
            bad.append(i)
            continue
        x = np.stack([data[k] for k in panels])
        if integer and info is not None:
            r = np.rint(x)
            if not np.array_equal(r, x) or r.min() < info.min or r.max() > info.max:
                rounded += 1
            x = np.clip(r, info.min, info.max)
        mm[i] = x
        if (j + 1) % 100 == 0:
            print("{} {} / {} shots ({:.0f} shots/s)".format(label, j + 1, len(items),
                                                              (j + 1) / max(time.time() - t0, 1e-6)), flush=True)
    mm.flush()
    del mm
    return bad, rounded


def _sum_part(payload: Tuple[str, Tuple[int, ...], str, List[int], List[int], Dict[str, Any], List[float],
                             List[str], Tuple[int, float, float]]) -> Dict[str, Any]:
    """Corrected sums per pump state, and the keV histograms of the single shots, of some shots."""
    path, shape, dtype, indices, states, corr_settings, kev, det_ids, rng = payload
    mm = np.memmap(path, dtype=dtype, mode="r", shape=shape)
    corr = Corrections(corr_settings, kev, det_ids)
    kev_a = np.asarray(kev, np.float32)
    ok = np.isfinite(kev_a)
    nb, lo, hi = rng
    n_panels = shape[1]
    sums = np.zeros((2,) + tuple(shape[1:]), np.float64)
    hist = np.zeros((2, nb), np.int64)
    n = np.zeros(2, np.int64)
    shot_f32 = int(np.prod(shape[1:])) * 4
    chunk = max(1, CHUNK_BYTES // shot_f32)
    idx = np.asarray(indices, np.int64)
    st = np.asarray(states, np.int64)
    threshold = bool(corr.settings.get("threshold"))
    for a in range(0, len(idx), chunk):
        ii, ss = idx[a:a + chunk], st[a:a + chunk]
        x = np.asarray(mm[ii], np.float32)                    # (k, panels, rows, cols)
        if corr.dark is not None:
            x -= corr.dark
        if threshold:
            x[x < corr.threshold_adu] = 0.0                   # threshold (panels, 1, 1)
        for s in (0, 1):
            m = ss == s
            if not m.any():
                continue
            sums[s] += x[m].sum(axis=0, dtype=np.float64)
            n[s] += int(m.sum())
            for k in range(n_panels):
                if not ok[k]:
                    continue
                v = x[m, k, ::HIST_STRIDE, ::HIST_STRIDE] * kev_a[k]
                b = np.floor((v - lo) * (nb / (hi - lo))).astype(np.int64).ravel()
                b = b[(b >= 0) & (b < nb)]
                hist[s] += np.bincount(b, minlength=nb)
    del mm
    return {"sums": sums, "n": n, "hist": hist}


# ====================================================================== the viewer's data
class OfflineRuns(object):
    """The loaded shots of stored runs and their averages for the viewer (see the module text)."""

    def __init__(self, cfg: Config, settings: Dict[str, Any], runs: Sequence[int],
                 max_tags: Optional[int] = None, every: int = 1, workers: int = 6,
                 where: str = "auto") -> None:
        import multiprocessing

        from silas.offline.scan_analysis import PanelReader

        self.cfg = cfg
        self.runs = [int(r) for r in runs]
        self.det_ids = list(settings["panels"])
        from silas.core import runner as _runner

        self.n_proc = _runner.cap_workers(max(1, int(workers)))
        bl = cfg.beamline
        print("offline view: runs {} ({}); finding the shots (dbpy) ...".format(
            ", ".join(str(r) for r in self.runs), ", ".join(self.det_ids)), flush=True)
        plan = plan_shots(cfg, self.runs, max_tags, every)
        self.items = plan["items"]   # type: List[Tuple[int, int, int]]
        self.counts = plan["counts"]  # type: Dict[str, int]
        self.has_pump = bool(plan["pump"])
        if not self.items:
            raise ValueError("no shots to load (X-rays off / pump state unknown for all?): {}".format(self.counts))

        reader = PanelReader(bl, self.det_ids)            # detector info and data type from the first shot
        run0, tag0, _ = self.items[0]
        first = reader.read(run0, tag0, list(range(len(self.det_ids))))
        if first is None:
            raise ValueError("run {} tag {}: the first shot cannot be read".format(run0, tag0))
        self._infos = [reader.info(k, run0, tag0) for k in range(len(self.det_ids))]
        shot = np.stack([first[k] for k in range(len(self.det_ids))])
        self.dtype = _storage_dtype(shot)
        self.shape = (len(self.items),) + tuple(shot.shape)  # type: Tuple[int, ...]
        nbytes = int(np.prod(self.shape)) * np.dtype(self.dtype).itemsize

        key = hashlib.sha1(json.dumps({"bl": bl, "runs": self.runs, "det": self.det_ids, "max": max_tags,
                                       "every": every, "items": len(self.items), "pump": plan["pump"],
                                       "dtype": self.dtype}, sort_keys=True).encode()).hexdigest()[:12]
        clean_ram_caches()
        room = ram_room()
        in_ram = where == "ram" or (where == "auto" and nbytes < room)
        if in_ram:
            self.path = os.path.join(SHM, "silas_{}_{}_{}.dat".format(getpass.getuser(), os.getpid(), key))
        else:
            self.path = os.path.join(work_folder(cfg), "mpccd_{}.dat".format(key))
        self.meta_path = self.path[:-4] + ".json"
        self.in_ram = in_ram
        self.place = "RAM ({})".format(SHM) if in_ram else self.path
        print("cache: {} shots x {} panels {} ({}) = {:.2f} GB in {}{}".format(
            self.shape[0], self.shape[1], "x".join(str(s) for s in self.shape[2:]), self.dtype, nbytes / 1e9,
            self.place, "" if in_ram or where != "auto" else " (RAM room {:.1f} GB)".format(room / 1e9)), flush=True)

        self.ctx = multiprocessing.get_context("spawn")
        self.pool = self.ctx.Pool(self.n_proc, initializer=runner.worker_ignores_ctrl_c)  # type: Any
        atexit.register(self.close)
        self.bad = set()  # type: Set[int]
        if not in_ram and self._cache_complete(key):
            print("cache found: no loading needed", flush=True)
        else:
            self._load(key, bl)
        self.use = [i for i in range(len(self.items)) if i not in self.bad]
        n_state = [sum(1 for i in self.use if self.items[i][2] == s) for s in (0, 1)]
        self.n_state = n_state
        self.job = None  # type: Any
        self.job_key = ""
        self.result_key = ""
        self.res = None  # type: Optional[Dict[str, Any]]
        self.new = False
        self.t_job = 0.0

    # ------------------------------------------------------------ loading
    def _cache_complete(self, key: str) -> bool:
        try:
            with open(self.meta_path) as f:
                meta = json.load(f)
        except (OSError, IOError, ValueError):
            return False
        if not (meta.get("complete") and meta.get("key") == key and tuple(meta.get("shape", ())) == self.shape
                and os.path.getsize(self.path) == int(np.prod(self.shape)) * np.dtype(self.dtype).itemsize):
            return False
        self.bad = set(meta.get("bad", []))
        return True

    def _load(self, key: str, bl: int) -> None:
        mm = np.memmap(self.path, dtype=self.dtype, mode="w+", shape=self.shape)   # allocate
        del mm
        jobs = [(i, run, tag) for i, (run, tag, _) in enumerate(self.items)]
        n = self.n_proc
        payloads = [(self.path, self.shape, self.dtype, bl, self.det_ids, jobs[p::n], "[load {}]".format(p))
                    for p in range(n)]
        t0 = time.time()
        print("loading {} shots with {} processes ...".format(len(jobs), n), flush=True)
        parts = self.pool.map(_load_part, payloads)
        rounded = sum(p[1] for p in parts)
        for p in parts:
            self.bad.update(p[0])
        print("loaded in {:.0f} s; unreadable shots: {}{}".format(
            time.time() - t0, len(self.bad),
            "; {} shots were not whole numbers in range and were rounded".format(rounded) if rounded else ""),
            flush=True)
        with open(self.meta_path, "w") as f:
            json.dump({"key": key, "shape": list(self.shape), "dtype": self.dtype, "complete": True,
                       "bad": sorted(self.bad), "runs": self.runs, "items": self.items,
                       "created": time.strftime("%Y-%m-%d %H:%M:%S")}, f)

    def infos(self) -> List[Dict[str, Any]]:
        return list(self._infos)

    # ------------------------------------------------------------ averages
    def request(self, corr_settings: Dict[str, Any], kev: Any, rng: Tuple[int, float, float]) -> str:
        """Start computing the averages for these corrections (if not done or running).
        Returns the key of the wanted result."""
        kev_l = [float(k) for k in kev]
        key = json.dumps([corr_settings, [round(k, 7) for k in kev_l], list(rng)], sort_keys=True)
        if key in (self.job_key, self.result_key):
            return key
        n = self.n_proc
        blocks = [self.use[a:a + 16] for a in range(0, len(self.use), 16)]   # spread evenly, read in order
        payloads = []  # type: List[Any]
        for p in range(n):
            idx = [i for b in blocks[p::n] for i in b]
            if idx:
                payloads.append((self.path, self.shape, self.dtype, idx, [self.items[i][2] for i in idx],
                                 dict(corr_settings), kev_l, self.det_ids, tuple(rng)))
        self.job = self.pool.map_async(_sum_part, payloads)
        self.job_key = key
        self.t_job = time.time()
        return key

    def poll(self) -> bool:
        """True once when a new result is ready."""
        if self.job is not None and self.job.ready():
            parts = self.job.get()
            self.job = None
            res = {"sums": sum(p["sums"] for p in parts), "n": sum(p["n"] for p in parts),
                   "hist": sum(p["hist"] for p in parts), "seconds": time.time() - self.t_job}
            self.res, self.result_key, self.job_key = res, self.job_key, ""
            self.new = True
        new, self.new = self.new, False
        return new

    def result(self, key: str) -> Optional[Dict[str, Any]]:
        return self.res if self.res is not None and key == self.result_key else None

    def busy(self) -> bool:
        return self.job is not None

    def describe(self, state: int) -> str:
        c = self.counts
        what = STATE_NAMES[state] if self.has_pump else "all shots (no pump detector)"
        return "runs {}: {} of {} shots loaded (X-rays off {}, pump unknown {}, unreadable {}); " \
               "on {} / off {}   SHOWING: {}{}".format(
                   ",".join(str(r) for r in self.runs), len(self.use), c["tags"], c["xray_off"],
                   c["pump_unknown"], len(self.bad), self.n_state[0], self.n_state[1], what.upper(),
                   "   computing ..." if self.busy() else "")

    def close(self) -> None:
        pool, self.pool = getattr(self, "pool", None), None
        if pool is not None:
            pool.terminate()
            pool.join()
        if getattr(self, "in_ram", False):
            for p in (self.path, self.meta_path):
                try:
                    os.remove(p)
                except OSError:
                    pass
