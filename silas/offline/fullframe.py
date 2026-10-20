"""Full-frame scan reduction (HPC, stpy): the ROI scan analysis, for every pixel.

    full_frame_job(1234501)                          # session: submit it as a batch job (qsub)
    reduce_full_frame(1234501, max_tags=100)         # session: run it here (a quick test on a bt_node)
    qsub -v SCAN=1234501,PANELS=1:2 ~/SILAS/scripts/fullframe_job.sh
    python -m silas.offline.fullframe --scan 1234501 --panels 1,2 --workers 16

    ff = load_full_frame(1234501)                    # session; at home: FullFrame("<file>.h5")
    ff.image("raw", step=10, state="on")             # (panels, rows, cols): median, keV per pixel per shot
    ff.onoff("i0_1", step=10)                        # (ratio, lower, upper error) of the medians

Everything the ROI analysis (silas.offline.scan_analysis) does with the value of an ROI is
done here with the value of every pixel of the chosen panels, with the same shots, the same
corrections and the same statistics:

  * shots: X-rays off / bad beam and unknown pump state are left out; pump on / off from the
    config's pump detector; a shot without a value for a reference is left out of everything
    computed with that reference
  * a pixel's value in a shot: (raw - dark), below the threshold -> 0, times keV per ADU of its
    panel: keV per pixel per shot (an ROI's value is the mean of these over its pixels)
  * per scan step (repeated steps combined) and pump state, over the shots: the 15.87 / 50 /
    84.13 % quantiles (numpy's linear interpolation, as np.quantile) and the MEAN. A pixel sees
    few photons per shot, so its median is often exactly 0 where an ROI's is not: the mean is
    what to use for weak signals (averaged over an ROI's pixels it is the ROI's mean)
  * referenced shot by shot: pixel / R of the shot, for every reference R
  * pump on / off = ratio of the medians, errors added in quadrature: computed when loading
    (FullFrame.onoff) from the stored on and off images, as scanroi.ratio_of_medians; pump
    on - off the same way (FullFrame.difference: of the medians, or of the means)
  * options as in analyse_scan: step_reference (median pixel / median R per step: the pixel
    medians over the shots WITH R are stored, the R quantiles are in the step table), pairs
    (every pump-on shot / and - the nearest pump-off shot of its run, quantiles of these)
  * time-tool binning (when on): the pump-on shots with a good edge in bins of their corrected
    timing (silas.core.ttbin, the same grid), per bin the statistics of the pixel, pixel / R
    and pump on / off and on - off step-wise (every shot / and - the median pump-off image of its
    step). Bins with
    fewer than ``min_shots`` shots (default: the config's scan.sparse_min) are listed in the bin
    table but get no images (NaN); min_shots=1 computes every bin. Non-finite values (x / 0 in
    an empty pixel) are left out of the statistics of ratios, as in the ROI binning.

Panels: only the chosen panels are read and stored (set_ff_panels, or panels=...; default all).

How (memory): one group of shots at a time (a scan step, later a time-tool bin). Its raw
panels are read once by the worker processes (stpy) into a buffer file (16-bit integers, in
RAM in /dev/shm if it fits in half the job's memory, else on /work), then the workers share the
pixels (blocks of rows of a panel) and compute all statistics of their block from the buffer:
about 2 MB per shot of two panels: a step of 2000 shots is 4 GB for 2 panels, 16 GB for 8.
Reading is limited by the storage (about 6 processes), the statistics by the cores: take 16.

Written to <output>/scans/scan_<id>/fullframe_<id>[_p<panels>].h5 (and .json with the info,
and _preview.png): see FullFrame and the docs page "Full-frame reduction" for the layout.
"""
import argparse
import atexit
import json
import math
import os
import shutil
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import energy, mpccd, sacla, scan, scanroi
from silas.core.config import Config

STATS = ("q16", "median", "q84", "mean")
"""Order of the statistics along the "stat" axis of every image dataset."""
STATE_NAMES = ("on", "off")
PANELS_KEY = "full_frame.panels"         # array setting: 1-based panel numbers, null = all
BLOCK_BYTES = 48 * 1024 ** 2             # float32 pixel values of one block (all shots of the group)
MAX_OPEN_RUNS = 8                        # stpy readers a worker keeps open (runs)
JOB_WORKERS = 16


# ====================================================================== statistics
def column_stats(V: np.ndarray, q: Sequence[float] = scanroi.QUANTILES) -> np.ndarray:
    """(4, b) float32: the quantiles ``q`` (linear interpolation, as np.quantile) and the mean
    of every row of V (b pixels, m shots), non-finite values left out; NaN where none is left."""
    b = V.shape[0]
    out = np.full((len(q) + 1, b), np.nan, np.float32)
    if b == 0 or V.shape[1] == 0:
        return out
    ok = np.isfinite(V)
    cnt = ok.sum(axis=1)
    have = cnt > 0
    if not have.any():
        return out
    S = np.where(ok, V, np.inf)                 # non-finite values sort to the end
    S.sort(axis=1)
    rows = np.arange(b)
    last = np.maximum(cnt - 1, 0)
    for i, qq in enumerate(q):
        p = qq * last
        lo = np.floor(p).astype(np.int64)
        hi = np.minimum(lo + 1, last)
        f = p - lo
        a = S[rows, lo].astype(np.float64)
        c = S[rows, hi].astype(np.float64)
        with np.errstate(invalid="ignore"):
            v = a + (c - a) * f
        out[i] = np.where(have, np.where(f > 0, v, a), np.nan)
    total = np.where(ok, V, 0.0).sum(axis=1, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[len(q)] = np.where(have, total / np.maximum(cnt, 1), np.nan)
    return out


# ====================================================================== panels (config)
def parse_panels(panels: Any, n_panels: int) -> List[int]:
    """0-based indices from 1-based panel numbers: [1, 2], "1,2", "1:2", 3; None / [] = all."""
    if panels is None or panels == "" or panels == []:
        return list(range(n_panels))
    if isinstance(panels, str):
        parts = [p for p in panels.replace(":", ",").replace(" ", ",").split(",") if p]
        nums = [int(p) for p in parts]
    elif isinstance(panels, (int, np.integer)):
        nums = [int(panels)]
    else:
        nums = [int(p) for p in panels]
    bad = [p for p in nums if not 1 <= p <= n_panels]
    if bad:
        raise ValueError("panels are numbered 1..{}: {} is not a panel".format(n_panels, bad))
    return sorted(set(p - 1 for p in nums))


def panel_choice(settings: Dict[str, Any], panels: Any = None) -> List[int]:
    """0-based indices of the panels to reduce: ``panels`` if given, else the array's
    full_frame.panels setting, else all."""
    if panels is None:
        panels = (settings.get("full_frame") or {}).get("panels")
    return parse_panels(panels, len(settings["panels"]))


def set_panels(cfg: Config, panels: Any, name: Optional[str] = None) -> List[int]:
    """Store the panels of the full-frame reduction (1-based numbers; None / [] = all)."""
    key, settings = mpccd.get_array(cfg, name)
    sel = parse_panels(panels, len(settings["panels"]))
    value = None if len(sel) == len(settings["panels"]) else [k + 1 for k in sel]
    mpccd.set_option(cfg, PANELS_KEY, value, key)
    return [k + 1 for k in sel]


def panels_tag(sel: Sequence[int], n_panels: int) -> str:
    """'' for all panels, else '_p' + the 1-based numbers (file name part), e.g. '_p12'."""
    if len(sel) == n_panels:
        return ""
    return "_p" + ("".join(str(k + 1) for k in sel) if n_panels < 10 else "-".join(str(k + 1) for k in sel))


def result_path(cfg: Config, scan_id: int, sel: Sequence[int], n_panels: int) -> str:
    return os.path.join(cfg.output_dir(), "scans", "scan_{}".format(scan_id),
                        "fullframe_{}{}.h5".format(scan_id, panels_tag(sel, n_panels)))


# ====================================================================== worker side
_WORKER = {}  # type: Dict[str, Any]


def _reader(bl: int, det_ids: Sequence[str]) -> Any:
    """The worker's stpy reader of these panels (kept between groups, see MAX_OPEN_RUNS)."""
    from silas.offline.scan_analysis import PanelReader

    key = (int(bl), tuple(det_ids))
    if _WORKER.get("reader_key") != key:
        _WORKER["reader_key"] = key
        _WORKER["reader"] = PanelReader(bl, det_ids)
        _WORKER["runs"] = []
    return _WORKER["reader"]


def _flags_job(payload: Tuple[int, int, List[int]]) -> Dict[str, Any]:
    """Shot flags of one run (scan_analysis.shot_flags) as lists."""
    from silas.offline.scan_analysis import shot_flags

    run, taghi, tags = payload
    cfg = _WORKER.get("cfg") or Config()
    _WORKER["cfg"] = cfg
    flags = shot_flags(cfg, cfg.beamline, int(run), int(taghi), tags)
    return {k: np.asarray(v) for k, v in flags.items()}


def _load_job(payload: Dict[str, Any]) -> Tuple[List[int], int, float]:
    """Read shots [(slot, run, tag)] of all chosen panels into the buffer. Returns (unreadable
    slots, shots that had to be rounded to the integer type, seconds)."""
    t0 = time.time()
    mm = np.memmap(payload["path"], dtype=payload["dtype"], mode="r+", shape=tuple(payload["shape"]))
    reader = _reader(payload["bl"], payload["det_ids"])
    panels = list(range(len(payload["det_ids"])))
    integer = np.issubdtype(np.dtype(payload["dtype"]), np.integer)
    info = np.iinfo(payload["dtype"]) if integer else None
    bad = []  # type: List[int]
    rounded = 0
    runs_here = []  # type: List[int]
    for slot, run, tag in payload["items"]:
        if run not in runs_here:
            runs_here.append(run)
        data = reader.read(run, tag, panels)
        if data is None:
            bad.append(int(slot))
            continue
        x = np.stack([np.asarray(data[k]) for k in panels])
        if integer and info is not None:
            r = np.rint(x)
            if not np.array_equal(r, x) or r.min() < info.min or r.max() > info.max:
                rounded += 1
            x = np.clip(r, info.min, info.max)
        mm[int(slot)] = x
    mm.flush()
    del mm
    order = [r for r in _WORKER.get("runs", []) if r not in runs_here] + runs_here
    while len(order) > MAX_OPEN_RUNS and hasattr(reader, "close_run"):
        reader.close_run(order.pop(0))
    _WORKER["runs"] = order
    return bad, rounded, time.time() - t0


def _block_job(p: Dict[str, Any]) -> float:
    """All statistics of one block of pixels (panel k, rows r0..r1) of a group (see Reduction)."""
    t0 = time.time()
    k, r0, r1 = p["k"], p["r0"], p["r1"]
    mm = np.memmap(p["buf_path"], dtype=p["buf_dtype"], mode="r", shape=tuple(p["buf_shape"]))
    x = np.asarray(mm[p["slots"], k, r0:r1, :], np.float32)       # (n, rows, cols) raw ADU
    del mm
    n, rows, cols = x.shape
    if p["dark"] is not None:
        x -= p["dark"]
    if p["thr"] is not None:
        x[x < np.float32(p["thr"])] = 0.0
    x *= np.float32(p["kev"])
    X = np.ascontiguousarray(x.reshape(n, rows * cols).T)        # (pixels, shots): keV per pixel
    del x
    refs = np.asarray(p["refs"], np.float32)                       # (n_refs, n)
    om = {}  # type: Dict[int, np.ndarray]
    if p.get("offmed"):
        path, shape = p["offmed"]
        omm = np.memmap(path, dtype=np.float32, mode="r", shape=tuple(shape))
        for v in sorted(set(t[4] for t in p["tasks"] if t[4] is not None)):
            om[v] = np.ascontiguousarray(np.asarray(omm[:, v, k, r0:r1, :]).reshape(shape[0], -1).T)
        del omm
    out = np.memmap(p["out_path"], dtype=np.float32, mode="r+", shape=tuple(p["out_shape"]))
    with np.errstate(invalid="ignore", divide="ignore"):
        for task in p["tasks"]:
            q, pos, ref, off, var, step_of = task[:6]
            diff = len(task) > 6 and task[6] == "diff"        # x - off instead of x / off
            pos = np.asarray(pos, np.int64)
            V = X[:, pos]
            if ref is not None:
                V = V / refs[ref, pos]
            if off is not None:
                off = np.asarray(off, np.int64)
                D = X[:, off]
                if ref is not None:
                    D = D / refs[ref, off]
                V = V - D if diff else V / D
            if var is not None:
                O = om[var][:, np.asarray(step_of, np.int64)]
                V = V - O if diff else V / O
            out[q, :, k, r0:r1, :] = column_stats(V).reshape(len(STATS), rows, cols)
    out.flush()
    del out
    return time.time() - t0


class _Serial(object):
    """A 'pool' that runs everything in this process (workers=1, tests)."""

    def map(self, func: Any, items: Sequence[Any], chunksize: int = 1) -> List[Any]:
        return [func(x) for x in items]

    def terminate(self) -> None:
        pass

    def join(self) -> None:
        pass


def _init_worker() -> None:
    from silas.core import runner

    runner.worker_ignores_ctrl_c()


# ====================================================================== scratch files
class Workspace(object):
    """Scratch files of one reduction: in /dev/shm if they fit in the RAM room of the job,
    else in the work folder (run_cache.work_folder); removed at the end."""

    def __init__(self, cfg: Config) -> None:
        from silas.offline import run_cache

        self.rc = run_cache
        tag = "silas_ff_{}_{}".format(os.getpid(), int(time.time()))
        self.ram_dir = os.path.join(run_cache.SHM, tag) if os.path.isdir(run_cache.SHM) else None
        self.disk_dir = os.path.join(run_cache.work_folder(cfg), tag)
        self.files = {}  # type: Dict[str, str]
        atexit.register(self.close)

    def file(self, name: str, shape: Sequence[int], dtype: Any, fill: Optional[float] = None) -> str:
        """A new memmap file (replacing the previous one of this name). Returns its path."""
        self.remove(name)
        nbytes = int(np.prod(shape)) * np.dtype(dtype).itemsize
        folder = self.ram_dir if self.ram_dir and nbytes < self.rc.ram_room() else self.disk_dir
        if not os.path.isdir(folder):
            os.makedirs(folder)
        path = os.path.join(folder, name + ".dat")
        mm = np.memmap(path, dtype=dtype, mode="w+", shape=tuple(int(s) for s in shape))
        if fill is not None:
            mm[...] = fill
        mm.flush()
        del mm
        self.files[name] = path
        return path

    def where(self, name: str) -> str:
        path = self.files.get(name, "")
        return "RAM" if self.ram_dir and path.startswith(self.ram_dir) else "disk ({})".format(os.path.dirname(path))

    def remove(self, name: str) -> None:
        path = self.files.pop(name, None)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

    def close(self) -> None:
        for name in list(self.files):
            self.remove(name)
        for d in (self.ram_dir, self.disk_dir):
            if d and os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)


# ====================================================================== HDF5 helpers
def _put_table(group: Any, frame: Any) -> None:
    """A DataFrame as one dataset per column (as scan_analysis.write_h5)."""
    for col in frame.columns:
        data = frame[col].to_numpy()
        if data.dtype == object and any(isinstance(v, str) for v in data):
            data = np.array([str(v) for v in data], dtype="S")       # text stays text (e.g. "runs")
        elif data.dtype == object:
            try:
                data = np.array([np.nan if v is None else float(v) for v in data], np.float64)
            except (TypeError, ValueError):
                data = np.array([str(v) for v in data], dtype="S")
        elif data.dtype == bool:
            data = data.astype(np.uint8)
        group.create_dataset(str(col), data=data)


def _image_dataset(f: Any, name: str, lead: Sequence[int], dims: str, panel_shape: Sequence[int]) -> Any:
    """Float32 image dataset of shape lead + (4 stats, panels, rows, cols), one panel image per
    chunk, gzip; unwritten parts read as NaN."""
    shape = tuple(int(s) for s in lead) + (len(STATS),) + tuple(int(s) for s in panel_shape)
    chunks = (1,) * (len(shape) - 2) + tuple(int(s) for s in panel_shape[-2:])
    ds = f.create_dataset(name, shape=shape, dtype="f4", chunks=chunks, compression="gzip",
                          compression_opts=1, shuffle=True, fillvalue=np.float32(np.nan))
    ds.attrs["dims"] = dims + ", stat, panel, row, col"
    ds.attrs["stats"] = json.dumps(list(STATS))
    return ds


# ====================================================================== the reduction
class Reduction(object):
    """One full-frame reduction of a scan (see the module text); run() does everything."""

    def __init__(self, cfg: Config, args: Any, pool: Any = None) -> None:
        self.cfg = cfg
        self.args = args
        self.key, self.settings = mpccd.get_array(cfg, getattr(args, "array", None))
        self.all_ids = list(self.settings["panels"])
        self.sel = panel_choice(self.settings, getattr(args, "panels", None))
        self.det_ids = [self.all_ids[k] for k in self.sel]
        self.refs = scanroi.reference_settings(cfg)
        self.labels = [label for label, _ in self.refs]
        self.pump, self.on_value = scanroi.pump_settings(cfg)
        self.n_states = 2 if self.pump else 1
        self.step_reference = bool(getattr(args, "step_reference", False))
        self.pairs = bool(getattr(args, "pairs", False)) and self.n_states == 2
        self.workers = max(1, int(getattr(args, "workers", 1) or 1))
        self.pool = pool
        self.timing = {}  # type: Dict[str, float]
        self.tmp_path = ""

    # ------------------------------------------------------------ helpers
    def _add_time(self, key: str, t: float) -> None:
        self.timing[key] = self.timing.get(key, 0.0) + t

    def _load(self, rows: Sequence[int]) -> Tuple[str, Tuple[int, ...], List[int], List[int]]:
        """Read the shots ``rows`` (index of self.shots) into the buffer. Returns (buffer path,
        shape, readable rows, their slots), rows in run and tag order."""
        sh = self.shots
        rows = sorted(rows, key=lambda r: (int(sh.at[r, "run"]), int(sh.at[r, "tag"])))
        shape = (len(rows),) + self.panel_shape
        path = self.ws.file("buffer", shape, self.dtype)
        items = [(i, int(sh.at[r, "run"]), int(sh.at[r, "tag"])) for i, r in enumerate(rows)]
        parts = [p for p in np.array_split(np.arange(len(items)), min(self.n_proc, max(1, len(items)))) if len(p)]
        payloads = [{"path": path, "shape": shape, "dtype": self.dtype, "bl": self.cfg.beamline,
                     "det_ids": self.det_ids, "items": [items[i] for i in part]} for part in parts]
        t0 = time.time()
        res = self.pool.map(_load_job, payloads)
        self._add_time("read", time.time() - t0)
        bad = set(b for part in res for b in part[0])
        self.rounded += sum(part[1] for part in res)
        ok = [i for i in range(len(rows)) if i not in bad]
        return path, shape, [rows[i] for i in ok], ok

    def _compute(self, buf: Tuple[str, Tuple[int, ...]], slots: List[int], refs: np.ndarray,
                 tasks: List[Tuple[Any, ...]], n_q: int, offmed: bool = False) -> np.ndarray:
        """Run the tasks on every block of pixels; returns the result (n_q, 4, panels, rows, cols)."""
        P, H, W = self.panel_shape
        out_shape = (max(n_q, 1), len(STATS), P, H, W)
        out_path = self.ws.file("result", out_shape, np.float32, fill=np.nan)
        n = max(1, len(slots))
        rows_per = int(max(1, min(H, BLOCK_BYTES // (n * W * 4))))
        payloads = []
        for k in range(P):
            for r0 in range(0, H, rows_per):
                payloads.append({"buf_path": buf[0], "buf_shape": buf[1], "buf_dtype": self.dtype,
                                 "slots": np.asarray(slots, np.int64), "k": k, "r0": r0,
                                 "r1": min(H, r0 + rows_per),
                                 "dark": None if self.dark is None else self.dark[k, r0:min(H, r0 + rows_per)],
                                 "thr": None if self.thr is None else float(self.thr[k]),
                                 "kev": float(self.kev[k]), "refs": refs, "tasks": tasks,
                                 "offmed": (self.ws.files["offmed"], self.offmed_shape) if offmed else None,
                                 "out_path": out_path, "out_shape": out_shape})
        t0 = time.time()
        if tasks and len(slots):
            self.pool.map(_block_job, payloads, chunksize=1)
        self._add_time("statistics", time.time() - t0)
        return np.array(np.memmap(out_path, dtype=np.float32, mode="r", shape=out_shape))

    def _ref_matrix(self, rows: Sequence[int]) -> np.ndarray:
        if not self.labels:
            return np.zeros((0, len(rows)), np.float32)
        return np.stack([self.shots.loc[list(rows), "ref_" + label].to_numpy(np.float64)
                         for label in self.labels]).astype(np.float32)

    # ------------------------------------------------------------ preparation
    def prepare(self) -> None:
        import pandas as pd

        from silas.core.corrections import Corrections
        from silas.offline import scan_analysis

        cfg = self.cfg
        sub = dict(self.settings, panels=self.det_ids)
        job = scan_analysis.prepare(cfg, sub, self.args.scan, getattr(self.args, "max_tags", None))
        self.sc = job["scan"]
        self.infos = job["infos"]
        first_run, first_tag = job["items"][0]
        reader = scan_analysis.PanelReader(cfg.beamline, self.det_ids)
        first = reader.read(first_run, first_tag, list(range(len(self.det_ids))))
        if first is None:
            raise ValueError("run {} tag {}: the first shot cannot be read".format(first_run, first_tag))
        from silas.offline.run_cache import _storage_dtype

        shot = np.stack([np.asarray(first[k]) for k in range(len(self.det_ids))])
        self.dtype = _storage_dtype(shot)
        self.panel_shape = tuple(int(s) for s in shot.shape)  # type: Tuple[int, ...]
        self.kev = energy.kev_per_adu(energy.absgains(self.infos), energy.ev_per_absgain_adu(self.settings))
        corr = Corrections(self.settings["corrections"], self.kev, self.det_ids)
        for m in corr.messages:
            print("corrections:", m)
        self.corr = corr
        self.dark = corr.dark
        self.thr = corr.threshold_adu.reshape(-1) if corr.settings.get("threshold") else None
        # shot flags of every run (dbpy), in the workers
        by_run = {}  # type: Dict[int, List[int]]
        for run, tag in job["items"]:
            by_run.setdefault(int(run), []).append(int(tag))
        t0 = time.time()
        payloads = [(run, int(job["run_info"][run].get("taghi") or 0) or _taghi(cfg, run), tags)
                    for run, tags in sorted(by_run.items())]
        flags = self.pool.map(_flags_job, payloads)
        self._add_time("flags", time.time() - t0)
        parts = []
        for (run, _, tags), fl in zip(payloads, flags):
            info = job["run_info"][run]
            d = {"run": np.full(len(tags), run, np.int64), "tag": np.asarray(tags, np.int64),
                 "step": info.get("step"), "value": info.get("value"),
                 "use": np.asarray(fl["use"], bool), "state": np.asarray(fl["state"], np.int64),
                 "read": False}  # type: Dict[str, Any]
            for label in self.labels:
                d["ref_" + label] = np.asarray(fl["ref_" + label], np.float64)
            parts.append(pd.DataFrame(d))
        self.shots = pd.concat(parts, ignore_index=True)
        self.template = scanroi.step_table(scanroi.ScanSums(1, self.n_states), self.sc["runs"])
        step_of_run = {}
        for j, st in enumerate(self.template):
            for r in st["runs"]:
                step_of_run[int(r)] = j
        self.shots["step_index"] = [step_of_run.get(int(r), -1) for r in self.shots["run"]]
        self.rounded = 0

    # ------------------------------------------------------------ steps
    def step_tasks(self, rows: List[int]) -> Tuple[List[Tuple[Any, ...]], List[Tuple[str, Any]], Dict[str, Any]]:
        """(tasks, [(dataset name, index) per task output], step-table values) of one step."""
        sh = self.shots.loc[rows]
        state = sh["state"].to_numpy(np.int64)
        tasks = []  # type: List[Tuple[Any, ...]]
        where = []  # type: List[Tuple[str, Any]]
        row = {}  # type: Dict[str, Any]
        names = STATE_NAMES if self.n_states == 2 else ("all",)

        def add(ds: str, idx: Any, pos: np.ndarray, ref: Optional[int] = None, off: Any = None,
                op: str = "ratio") -> None:
            tasks.append((len(tasks), pos, ref, off, None, None, op))
            where.append((ds, idx))

        for s in range(self.n_states):
            pos = np.nonzero(state == s)[0]
            row["n_" + names[s]] = int(len(pos))
            add("raw", s, pos)
        for i, label in enumerate(self.labels):
            R = sh["ref_" + label].to_numpy(np.float64)
            valid = np.isfinite(R)
            for s in range(self.n_states):
                pos = np.nonzero(valid & (state == s))[0]
                row["n_{}_{}".format(names[s], label)] = int(len(pos))
                add("per_" + label, s, pos, ref=i)
                if self.step_reference:
                    add("raw_with_" + label, s, pos)
                vals = R[pos]
                qa = np.quantile(vals, list(scanroi.QUANTILES)) if len(vals) else [math.nan] * 3
                for qn, v in zip(("q16", "median", "q84"), qa):
                    row["{}_{}_{}".format(label, names[s], qn)] = float(v)
        if self.pairs:
            which = [(None, None)] + [(r, lb) for r, lb in enumerate(self.labels)]  # type: List[Tuple[Optional[int], Optional[str]]]
            for ref_i, ref_label in which:
                ok = np.ones(len(sh), bool) if ref_label is None else np.isfinite(sh["ref_" + ref_label].to_numpy(np.float64))
                on_all, off_all = [], []  # type: List[np.ndarray], List[np.ndarray]
                for run in sorted(set(int(r) for r in sh["run"])):
                    in_run = (sh["run"].to_numpy() == run) & ok
                    tags = sh["tag"].to_numpy(np.int64)
                    on = np.nonzero(in_run & (state == 0))[0]
                    off = np.nonzero(in_run & (state == 1))[0]
                    if not len(on) or not len(off):
                        continue
                    on = on[np.argsort(tags[on], kind="stable")]
                    off = off[np.argsort(tags[off], kind="stable")]
                    j = scanroi.nearest_off(tags[on], tags[off])
                    on_all.append(on)
                    off_all.append(off[j])
                on_p = np.concatenate(on_all) if on_all else np.zeros(0, np.int64)
                off_p = np.concatenate(off_all) if off_all else np.zeros(0, np.int64)
                name = "pairs_raw" if ref_label is None else "pairs_per_" + ref_label
                row["n_pairs" + ("" if ref_label is None else "_" + ref_label)] = int(len(on_p))
                add(name, None, on_p, ref=ref_i, off=off_p)
                add(name.replace("pairs_", "pairs_diff_"), None, on_p, ref=ref_i, off=off_p, op="diff")
        return tasks, where, row

    def steps(self, f: Any) -> None:
        """Every scan step: read, statistics, into the file."""
        import pandas as pd

        n_steps = len(self.template)
        P, H, W = self.panel_shape
        ds = {}  # type: Dict[str, Any]
        dims = "step, state ({})".format(", ".join(STATE_NAMES if self.n_states == 2 else ("all",)))
        ds["raw"] = _image_dataset(f, "steps/raw", (n_steps, self.n_states), dims, self.panel_shape)
        for label in self.labels:
            ds["per_" + label] = _image_dataset(f, "steps/per_" + label, (n_steps, self.n_states), dims,
                                                self.panel_shape)
            if self.step_reference:
                ds["raw_with_" + label] = _image_dataset(f, "steps/raw_with_" + label, (n_steps, self.n_states),
                                                         dims, self.panel_shape)
        if self.pairs:
            for name in ["pairs_raw"] + ["pairs_per_" + label for label in self.labels]:
                for nm in (name, name.replace("pairs_", "pairs_diff_")):
                    ds[nm] = _image_dataset(f, "steps/" + nm, (n_steps,), "step", self.panel_shape)
        if self.offmed_shape is not None:
            self.ws.file("offmed", self.offmed_shape, np.float32, fill=np.nan)
        table = []  # type: List[Dict[str, Any]]
        for j, st in enumerate(self.template):
            t0 = time.time()
            in_step = self.shots["run"].isin(st["runs"]).to_numpy() & self.shots["use"].to_numpy(bool) \
                & (self.shots["state"].to_numpy() >= 0)
            rows = list(np.nonzero(in_step)[0])
            path, shape, ok_rows, slots = self._load(rows)
            self.shots.loc[ok_rows, "read"] = True
            t1 = time.time()
            tasks, where, row = self.step_tasks(ok_rows)
            res = self._compute((path, shape), slots, self._ref_matrix(ok_rows), tasks, len(tasks))
            t2 = time.time()
            for (name, idx), q in zip(where, range(len(tasks))):
                if idx is None:
                    ds[name][j] = res[q]
                else:
                    ds[name][j, idx] = res[q]
            if self.offmed_shape is not None:
                om = np.memmap(self.ws.files["offmed"], dtype=np.float32, mode="r+", shape=self.offmed_shape)
                for q, (name, idx) in enumerate(where):
                    if idx == 1 and name == "raw":
                        om[j, 0] = res[q, 1]
                    elif idx == 1 and name.startswith("per_"):
                        om[j, 1 + self.labels.index(name[4:])] = res[q, 1]
                om.flush()
                del om
            self._add_time("write", time.time() - t2)
            row.update({"x": st["x"], "step": st["step"], "value": st["value"],
                        "runs": ";".join(str(r) for r in st["runs"]), "n_read": len(ok_rows),
                        "n_unreadable": len(rows) - len(ok_rows)})
            table.append(row)
            print("step {:>3}/{} (runs {}): {} shots ({}); read {:.1f} s, statistics {:.1f} s".format(
                j + 1, n_steps, ",".join(str(r) for r in st["runs"]), len(ok_rows),
                ", ".join("{} {}".format(row.get("n_" + n, 0), n)
                          for n in (STATE_NAMES if self.n_states == 2 else ("all",))),
                t1 - t0, t2 - t1), flush=True)
        self.step_frame = pd.DataFrame(table)
        first = ["x", "step", "value", "runs", "n_read", "n_unreadable"]
        self.step_frame = self.step_frame[first + [c for c in self.step_frame.columns if c not in first]]
        self.x_is_value = bool(self.template and self.template[0]["x_is_value"])

    # ------------------------------------------------------------ time-tool bins
    def bins(self, f: Any) -> Optional[Dict[str, Any]]:
        from silas.core import timetool, ttbin
        from silas.offline import scan_analysis

        bin_ps = float(self.args.bin_ps) if getattr(self.args, "bin_ps", None) else \
            float(ttbin.settings(self.cfg)["tt_bin_ps"])
        scan_analysis.make_missing_tt_log(self.cfg, sorted(set(int(r) for r in self.shots["run"])), self.n_proc)
        notes = ttbin.attach_timing(self.cfg, self.shots, self.sc.get("motor_true"))
        if notes["unit_warning"]:
            print(notes["unit_warning"])
        if notes["no_calibration"]:
            print("time-tool binning: no time-tool calibration (calibrate_tt): no bins")
            return None
        if notes["missing_runs"]:
            print("time-tool binning: runs without a time-tool log, left out: {}".format(
                ", ".join(str(r) for r in notes["missing_runs"])))
        ps = float(timetool.settings(self.cfg).get("ps_per_pulse", 0.006671))
        btab, grid = ttbin.bin_table(self.shots, [], bin_ps / ps, ps)
        if not len(btab):
            print("time-tool binning: no pump-on shot with a good edge and a motor value: no bins")
            return None
        min_shots = int(self.args.min_shots) if getattr(self.args, "min_shots", None) else \
            int(ttbin.settings(self.cfg)["sparse_min"])
        btab["computed"] = (btab["n"].to_numpy(np.int64) >= max(1, min_shots)).astype(np.uint8)
        n_bins = len(btab)
        ds = {"raw": _image_dataset(f, "bins/raw", (n_bins,), "bin", self.panel_shape)}
        for label in self.labels:
            ds["per_" + label] = _image_dataset(f, "bins/per_" + label, (n_bins,), "bin", self.panel_shape)
        two = self.n_states == 2
        if two:
            for kind in ("onoff", "diff"):
                ds[kind + "_raw"] = _image_dataset(f, "bins/{}_raw".format(kind), (n_bins,), "bin", self.panel_shape)
                for label in self.labels:
                    ds["{}_per_{}".format(kind, label)] = _image_dataset(
                        f, "bins/{}_per_{}".format(kind, label), (n_bins,), "bin", self.panel_shape)
        extra = {"n_read": [], "n_onoff": []}  # type: Dict[str, List[int]]
        for label in self.labels:
            extra["n_" + label] = []
        n_done = int(btab["computed"].sum())
        print("time-tool binning: {} bins of {:g} ps; images for the {} bins with >= {} shots".format(
            n_bins, bin_ps, n_done, min_shots), flush=True)
        done = 0
        for i, b in enumerate(btab["bin"].to_numpy(np.int64)):
            rows = list(np.nonzero(self.shots["bin"].to_numpy(np.int64) == b)[0])
            if not btab["computed"].iloc[i]:
                extra["n_read"].append(0)
                extra["n_onoff"].append(0)
                for label in self.labels:
                    extra["n_" + label].append(0)
                continue
            t0 = time.time()
            path, shape, ok_rows, slots = self._load(rows)
            sh = self.shots.loc[ok_rows]
            step_of = sh["step_index"].to_numpy(np.int64)
            allpos = np.arange(len(ok_rows))
            tasks = [(0, allpos, None, None, None, None)]  # type: List[Tuple[Any, ...]]
            where = [("raw", None)]
            extra["n_read"].append(len(ok_rows))
            for r, label in enumerate(self.labels):
                pos = np.nonzero(np.isfinite(sh["ref_" + label].to_numpy(np.float64)))[0]
                extra["n_" + label].append(int(len(pos)))
                tasks.append((len(tasks), pos, r, None, None, None))
                where.append(("per_" + label, None))
            if two:
                extra["n_onoff"].append(len(ok_rows))
                for kind, op in (("onoff", "ratio"), ("diff", "diff")):
                    tasks.append((len(tasks), allpos, None, None, 0, step_of, op))
                    where.append((kind + "_raw", None))
                    for r, label in enumerate(self.labels):
                        pos = np.nonzero(np.isfinite(sh["ref_" + label].to_numpy(np.float64)))[0]
                        tasks.append((len(tasks), pos, r, None, 1 + r, step_of[pos], op))
                        where.append(("{}_per_{}".format(kind, label), None))
            else:
                extra["n_onoff"].append(0)
            res = self._compute((path, shape), slots, self._ref_matrix(ok_rows), tasks, len(tasks), offmed=two)
            t2 = time.time()
            for q, (name, _) in enumerate(where):
                ds[name][i] = res[q]
            self._add_time("write", time.time() - t2)
            done += 1
            print("bin {:>4} ({}/{}, t = {:.3f} ps): {} shots; {:.1f} s".format(
                int(b), done, n_done, float(btab["t_ps"].iloc[i]), len(ok_rows), time.time() - t0), flush=True)
        for k, v in extra.items():
            btab[k] = v
        info = {"bin_ps": bin_ps, "bin_pulse": bin_ps / ps, "ps_per_pulse": ps, "t0_pulse": grid.t0,
                "bins": n_bins, "bins_with_images": n_done, "min_shots": min_shots,
                "missing_runs": notes["missing_runs"], "calibration": timetool.settings(self.cfg).get("calibration"),
                "onoff": "step-wise: every pump-on shot / the median pump-off image of its step",
                "diff": "step-wise: every pump-on shot - the median pump-off image of its step"}
        return {"table": btab, "info": info}

    # ------------------------------------------------------------ all
    def run(self) -> Optional[str]:
        import pandas as pd

        from silas.core import ttbin

        t_start = time.time()
        own_pool = self.pool is None
        from silas.core import runner

        self.n_proc = runner.cap_workers(self.workers) if self.workers > 1 else 1
        if own_pool:
            if self.n_proc > 1:
                import multiprocessing

                ctx = multiprocessing.get_context("spawn")
                self.pool = ctx.Pool(self.n_proc, initializer=_init_worker)
            else:
                self.pool = _Serial()
        self.ws = Workspace(self.cfg)
        try:
            self.prepare()
            sc = self.sc
            P, H, W = self.panel_shape
            tt_arg = getattr(self.args, "tt", None)
            self.tt_on = bool(ttbin.settings(self.cfg)["tt_binning"]) if tt_arg is None else bool(tt_arg)
            self.offmed_shape = (len(self.template), 1 + len(self.labels), P, H, W) \
                if (self.tt_on and self.n_states == 2) else None
            per_image = P * H * W * 4
            n_img = len(self.template) * len(STATS) * (
                self.n_states * (1 + len(self.labels) * (2 if self.step_reference else 1))
                + (2 * (1 + len(self.labels)) if self.pairs else 0))
            use = self.shots["use"].to_numpy(bool) & (self.shots["state"].to_numpy() >= 0)
            biggest = max([int((self.shots["run"].isin(st["runs"]).to_numpy() & use).sum()) for st in self.template]
                          or [0])
            print("full-frame reduction of scan {} ({} runs, {} steps, {} shots), {} panels {} of {} ({}), "
                  "references: {}, pump: {}".format(
                      sc["scan_id"], len(sc["runs"]), len(self.template), len(self.shots), len(self.sel),
                      ",".join(str(k + 1) for k in self.sel), self.key, "x".join(str(s) for s in self.panel_shape[1:]),
                      ", ".join(self.labels) or "none", self.pump or "none"), flush=True)
            print("steps: {} images (all chosen panels) of {:.1f} MB = {:.1f} GB before compression{}; largest "
                  "step {} shots: buffer {:.1f} GB; {} worker processes".format(
                      n_img, per_image / 1e6, n_img * per_image / 1e9,
                      " (+ the time-tool bins)" if self.tt_on else "", biggest,
                      biggest * P * H * W * np.dtype(self.dtype).itemsize / 1e9, self.n_proc), flush=True)
            out = result_path(self.cfg, sc["scan_id"], self.sel, len(self.all_ids))
            if getattr(self.args, "out", None):
                out = os.path.abspath(os.path.expanduser(self.args.out))
            folder = os.path.dirname(out)
            if not os.path.isdir(folder):
                os.makedirs(folder)
            import h5py

            tmp = out + ".tmp"
            self.tmp_path = tmp
            with h5py.File(tmp, "w") as f:
                self.steps(f)
                tt = self.bins(f) if self.tt_on else None
                info = self.info(tt, out)
                _put_table(f.create_group("shots"), self.shots)
                _put_table(f.create_group("step_table"), self.step_frame)
                if tt is not None:
                    _put_table(f.create_group("bin_table"), tt["table"])
                f.attrs["info"] = json.dumps(info, default=_json_default)
                f.attrs["stats"] = json.dumps(list(STATS))
                f.attrs["quantiles"] = np.asarray(scanroi.QUANTILES)
            os.replace(tmp, out)
            info["timing_s"]["total"] = time.time() - t_start
            with open(out[:-3] + ".json", "w") as fh:
                json.dump(info, fh, indent=1, default=_json_default)
            size = os.path.getsize(out) / 1e9
            print("written {} ({:.2f} GB) in {:.0f} s (read {:.0f} s, statistics {:.0f} s, writing {:.0f} s)".format(
                out, size, time.time() - t_start, self.timing.get("read", 0.0), self.timing.get("statistics", 0.0),
                self.timing.get("write", 0.0)), flush=True)
            try:
                preview(out, out[:-3] + "_preview.png")
            except Exception as err:  # noqa: BLE001  the preview is only a convenience
                print("preview not made: {}".format(err))
            return out
        finally:
            if own_pool and self.pool is not None:
                self.pool.terminate()
                self.pool.join()
            self.ws.close()
            if self.tmp_path and os.path.exists(self.tmp_path):    # stopped half-way
                os.remove(self.tmp_path)

    def info(self, tt: Optional[Dict[str, Any]], path: str) -> Dict[str, Any]:
        sh = self.shots
        g = self.settings["geometry"]
        offsets = g.get("panel_offsets") or {}
        sel_offsets = {str(i + 1): offsets[str(k + 1)] for i, k in enumerate(self.sel) if str(k + 1) in offsets}
        point = self.cfg.get("point_detectors", {})
        names = list(STATE_NAMES) if self.n_states == 2 else ["all"]
        layout = {"steps/raw": "step, state ({}), stat, panel, row, col".format(", ".join(names))}
        for label in self.labels:
            layout["steps/per_" + label] = layout["steps/raw"] + " (pixel / {} per shot)".format(label)
            if self.step_reference:
                layout["steps/raw_with_" + label] = layout["steps/raw"] + " (shots with {} only)".format(label)
        if self.pairs:
            layout["steps/pairs_raw"] = "step, stat, panel, row, col (pump-on / nearest pump-off shot)"
            layout["steps/pairs_diff_raw"] = "step, stat, panel, row, col (pump-on - nearest pump-off shot)"
        if tt is not None:
            layout["bins/raw"] = "bin, stat, panel, row, col (pump-on shots)"
            layout["bins/onoff_raw"] = "bin, stat, panel, row, col (shot / median off image of its step)"
            layout["bins/diff_raw"] = "bin, stat, panel, row, col (shot - median off image of its step)"
        info = {"what": "full-frame reduction", "file": os.path.basename(path),
                "scan": {k: v for k, v in self.sc.items() if k != "runs"}, "runs": self.sc["runs"],
                "array": self.key, "panel_numbers": [k + 1 for k in self.sel], "panels": self.det_ids,
                "panel_shape": list(self.panel_shape[1:]), "detector_info": self.infos,
                "geometry": {"rounding": g.get("rounding", "floor"), "sub_pixel": list(g.get("sub_pixel", (0.0, 0.0))),
                             "panel_offsets": sel_offsets},
                "corrections": self.settings["corrections"], "calibration": self.settings.get("calibration"),
                "keV_per_adu": [float(k) for k in self.kev], "photon_energy_keV": energy.photon_energy(
                    self.cfg, int(self.sc["runs"][0]["run"])),
                "states": names, "pump_detector": self.pump, "pump_on_value": self.on_value,
                "references": {label: {"detector": true, "unit": str((point.get(true) or {}).get("unit") or "")}
                               for label, true in self.refs},
                "stats": list(STATS), "quantiles": list(scanroi.QUANTILES),
                "units": "keV per pixel per shot (per reference unit when referenced)",
                "x_is_value": self.x_is_value,
                "error_bars": "spread of the shots: quantiles 15.87 / 84.13 % (not divided by sqrt(n)); on / off: "
                              "ratio of medians, relative errors added in quadrature (FullFrame.onoff)",
                "options": {"step_reference": self.step_reference, "pairs": self.pairs, "tt_binning": self.tt_on},
                "tt_binning": tt["info"] if tt is not None else None,
                "shots": {"total": int(len(sh)), "used": int(sh["read"].sum()), "xray_off": int((~sh["use"]).sum()),
                          "pump_unknown": int(((sh["state"] < 0) & sh["use"]).sum()),
                          "rounded_to_integer": int(self.rounded)},
                "storage": self.dtype, "max_tags": getattr(self.args, "max_tags", None), "workers": self.n_proc,
                "layout": layout, "made": time.strftime("%Y-%m-%d %H:%M:%S"),
                "timing_s": dict(self.timing)}
        return info


def _taghi(cfg: Config, run: int) -> int:
    from silas.core import db

    return int(db.high_tag(cfg.beamline, run))


def _json_default(v: Any) -> Any:
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, bytes):
        return v.decode(errors="replace")
    return str(v)


# ====================================================================== reading the result
class FullFrame(object):
    """A full-frame reduction file (the HPC result, or a copy at home: needs h5py, numpy, pandas).

        ff = FullFrame("fullframe_1234501.h5")
        ff.steps                                  # step table (x, runs, shot counts, reference quantiles)
        ff.image("raw", step=10, state="on")      # median image (panels, rows, cols), keV per pixel per shot
        ff.image("i0_1", step=10, state="off", stat="mean")   # pixel / i0_1 per shot, mean over the shots
        ratio, lo, hi = ff.onoff("raw", step=10)  # pump on / off of the medians, errors in quadrature
        d, lo, hi = ff.difference("raw", step=10) # pump on - off of the medians (stat="mean": of the means)
        ff.image("raw", bin=3)                    # a time-tool bin (pump on); ff.image("onoff", bin=3)
        img = ff.assemble(ratio)                  # one 2-D image of the panels (detector geometry)

    kind: "raw", a reference label (pixel / R per shot), "raw_with_<label>" (step_reference),
    "pairs" / "pairs_<label>" and "pairs_diff" / "pairs_diff_<label>" (steps, pairs option: on / off and
    on - off of nearest-off pairs), "onoff" / "onoff_<label>" and "diff" / "diff_<label>" (bins: every
    shot / and - the median off image of its step). Steps: ff.onoff(...) and ff.difference(...).
    step / bin: the index in the step / bin table (0 = first); state: "on", "off" (or "all")."""

    def __init__(self, path: str) -> None:
        import h5py

        self.path = path
        self.f = h5py.File(path, "r")
        self.info = json.loads(self.f.attrs["info"])
        self.stats = list(self.info.get("stats", STATS))
        self.states = list(self.info.get("states", STATE_NAMES))
        self.labels = list(self.info.get("references", {}))
        self.shots = self._table("shots")
        self.steps = self._table("step_table")
        self.bins = self._table("bin_table") if "bin_table" in self.f else None  # type: Any
        self._geom = None  # type: Any

    def _table(self, name: str) -> Any:
        import pandas as pd

        data = {}
        for k in self.f[name]:
            v = self.f[name][k][()]
            if getattr(v, "dtype", None) is not None and v.dtype.kind == "S":
                v = v.astype(str)
            data[k] = v
        return pd.DataFrame(data)

    def _dataset(self, kind: str, binned: bool) -> Tuple[Any, bool]:
        """(dataset, has a state axis)."""
        group = "bins" if binned else "steps"
        name = kind                                     # a dataset name as it is ("per_i0_1", ...)
        if kind == "raw":
            name = "raw"
        elif kind in self.labels:
            name = "per_" + kind
        elif kind in ("pairs", "pairs_diff", "onoff", "diff"):
            name = kind + "_raw"
        else:
            for prefix in ("pairs_diff_", "pairs_", "onoff_", "diff_"):   # "pairs_diff_" before "pairs_"
                rest = kind[len(prefix):]
                if kind.startswith(prefix) and rest in self.labels:
                    name = prefix + "per_" + rest
                    break
        path = "{}/{}".format(group, name)
        if path not in self.f:
            have = sorted("{}/{}".format(g, k) for g in ("steps", "bins") if g in self.f for k in self.f[g])
            raise KeyError("no {} in {} (there are: {})".format(path, self.path, ", ".join(have)))
        ds = self.f[path]
        return ds, (not binned) and ds.ndim == 6

    def image(self, kind: str = "raw", step: Optional[int] = None, state: str = "on", stat: str = "median",
              bin: Optional[int] = None) -> np.ndarray:
        """One image (panels, rows, cols) as float32; NaN where nothing was computed."""
        if (step is None) == (bin is None):
            raise ValueError("give step=<index> or bin=<index>")
        ds, has_state = self._dataset(kind, bin is not None)
        si = self.stats.index(stat)
        i = int(step if step is not None else bin)  # type: ignore[arg-type]
        if has_state:
            return np.asarray(ds[i, self.states.index(state), si], np.float32)
        return np.asarray(ds[i, si], np.float32)

    def stack(self, kind: str = "raw", state: str = "on", stat: str = "median", binned: bool = False) -> np.ndarray:
        """All steps (or bins) at once: (steps, panels, rows, cols). Large: one panel image each."""
        ds, has_state = self._dataset(kind, binned)
        si = self.stats.index(stat)
        if has_state:
            return np.asarray(ds[:, self.states.index(state), si], np.float32)
        return np.asarray(ds[:, si], np.float32)

    def onoff(self, kind: str = "raw", step: int = 0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Pump on / off of the medians of a step with lower / upper errors (scanroi.ratio_of_medians,
        as the ROI analysis): kind "raw" or a reference label (per shot referenced)."""
        if len(self.states) != 2:
            raise ValueError("no pump states in this reduction")
        q = {s: {st: self.image(kind, step=step, state=s, stat=st) for st in ("q16", "median", "q84")}
             for s in ("on", "off")}
        a, b = q["on"], q["off"]
        return scanroi.ratio_of_medians(a["median"], a["median"] - a["q16"], a["q84"] - a["median"],
                                        b["median"], b["median"] - b["q16"], b["q84"] - b["median"])

    def difference(self, kind: str = "raw", step: int = 0, stat: str = "median") -> Tuple[np.ndarray, np.ndarray,
                                                                                        np.ndarray]:
        """Pump on - pump off of a step with lower / upper errors: of the medians (stat="median"; the
        errors of the two added in quadrature, the lower error from the lower error of on and the upper
        of off) or of the means (stat="mean"; errors NaN). kind "raw" or a reference label."""
        if len(self.states) != 2:
            raise ValueError("no pump states in this reduction")
        if stat == "mean":
            d = self.image(kind, step=step, state="on", stat="mean") - self.image(kind, step=step, state="off",
                                                                                 stat="mean")
            nan = np.full_like(d, np.nan)
            return d, nan, nan
        q = {s: {st: self.image(kind, step=step, state=s, stat=st) for st in ("q16", "median", "q84")}
             for s in ("on", "off")}
        a, b = q["on"], q["off"]
        lo = np.sqrt((a["median"] - a["q16"]) ** 2 + (b["q84"] - b["median"]) ** 2)
        hi = np.sqrt((a["q84"] - a["median"]) ** 2 + (b["median"] - b["q16"]) ** 2)
        return a["median"] - b["median"], lo, hi

    def step_referenced(self, label: str, step: int = 0, state: str = "on") -> Tuple[np.ndarray, np.ndarray,
                                                                                     np.ndarray]:
        """Median pixel / median reference of a step (step_reference option), errors in quadrature."""
        a = {st: self.image("raw_with_" + label, step=step, state=state, stat=st) for st in ("q16", "median", "q84")}
        row = self.steps.iloc[int(step)]
        m, lo_, hi_ = (float(row["{}_{}_{}".format(label, state, k)]) for k in ("median", "q16", "q84"))
        return scanroi.ratio_of_medians(a["median"], a["median"] - a["q16"], a["q84"] - a["median"],
                                        np.array(m, np.float32), np.array(m - lo_, np.float32),
                                        np.array(hi_ - m, np.float32))

    def geometry(self) -> Any:
        """silas.core.geometry.Geometry of the stored panels (as the analysis used it)."""
        if self._geom is None:
            from silas.core.geometry import Geometry

            g = self.info.get("geometry", {})
            shape = tuple(self.info["panel_shape"])
            geom = Geometry.from_infos(self.info["panels"], self.info["detector_info"],
                                       shapes=[shape] * len(self.info["panels"]),
                                       rounding=g.get("rounding", "floor"), sub_pixel=tuple(g.get("sub_pixel", (0, 0))))
            self._geom = geom.tweaked(panel_offsets=g.get("panel_offsets") or {})
        return self._geom

    def assemble(self, image: np.ndarray) -> np.ndarray:
        """One 2-D image (detector geometry, centred on the origin; gaps NaN) from (panels, rows, cols).
        The values are not rescaled (they are keV already)."""
        return self.geometry().assemble(list(np.asarray(image)), gain=False)

    def close(self) -> None:
        self.f.close()

    def __repr__(self) -> str:
        return "FullFrame({!r}: scan {}, panels {}, {} steps{})".format(
            self.path, self.info["scan"].get("scan_id"), self.info.get("panel_numbers"), len(self.steps),
            ", {} bins".format(len(self.bins)) if self.bins is not None else "")


def preview(path: str, png: str) -> None:
    """Quick look: median pump-on image of the first and the last step, and their on / off ratio
    (assembled), or the medians only without pump states."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ff = FullFrame(path)
    try:
        n = len(ff.steps)
        two = len(ff.states) == 2
        state = "on" if two else "all"
        panels = [("median {} step 1".format(state), ff.image("raw", step=0, state=state)),
                  ("median {} step {}".format(state, n), ff.image("raw", step=n - 1, state=state))]
        if two:
            with np.errstate(invalid="ignore", divide="ignore"):
                panels.append(("on / off of the medians, step {}".format(n), ff.onoff("raw", step=n - 1)[0]))
                panels.append(("on / off of the means, step {}".format(n),
                               ff.image("raw", step=n - 1, state="on", stat="mean")
                               / ff.image("raw", step=n - 1, state="off", stat="mean")))
        fig, axes = plt.subplots(1, len(panels), figsize=(5 * len(panels), 5), squeeze=False)
        for ax, (title, img) in zip(axes[0], panels):
            a = ff.assemble(img)
            fin = a[np.isfinite(a)]
            lo, hi = (np.percentile(fin, [1, 99.7]) if fin.size else (0, 1))
            if "/" in title and fin.size:
                lo, hi = np.percentile(fin, [2, 98])
            im = ax.imshow(a, cmap="viridis", vmin=lo, vmax=hi)
            ax.set_title(title, fontsize=9)
            fig.colorbar(im, ax=ax, fraction=0.046)
        fig.suptitle("full-frame reduction, scan {}, panels {}".format(ff.info["scan"].get("scan_id"),
                                                                       ff.info.get("panel_numbers")))
        fig.savefig(png, dpi=90)
        plt.close(fig)
    finally:
        ff.close()


# ====================================================================== main
@sacla.requires_sacla
def run_live(args: Any) -> None:
    Reduction(Config(), args).run()


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="full-frame scan reduction (stpy): the ROI analysis for every pixel")
    ap.add_argument("--scan", type=int, default=-1, help="-1 newest (default), -2, 0, 1, ... or a run number")
    ap.add_argument("--array", help="MPCCD array name or alias (default: use_mpccd)")
    ap.add_argument("--panels", help="panels (1-based), e.g. 1,2 or 1:2 (default: set_ff_panels, else all)")
    ap.add_argument("--max-tags", type=int, help="only this many shots per run (evenly spread), for a quick test")
    ap.add_argument("--workers", type=int, default=JOB_WORKERS,
                    help="processes on this node (at most the job's cores; default {})".format(JOB_WORKERS))
    ap.add_argument("--step-reference", action="store_true", help="also the pixel medians of the shots with R")
    ap.add_argument("--pairs", action="store_true", help="also on / off of nearest-off pairs")
    ap.add_argument("--tt", dest="tt", action="store_true", default=None,
                    help="time-tool binning on (default: the config's scan.tt_binning)")
    ap.add_argument("--no-tt", dest="tt", action="store_false", help="time-tool binning off")
    ap.add_argument("--bin-ps", type=float, help="time-tool bin width in ps (default: the config's scan.tt_bin_ps)")
    ap.add_argument("--min-shots", type=int,
                    help="time-tool bins with fewer shots get no images (default: the config's scan.sparse_min)")
    ap.add_argument("--out", help="output file (default: <output>/scans/scan_<id>/fullframe_<id>[_p...].h5)")
    run_live(ap.parse_args(argv))


if __name__ == "__main__":
    main()
