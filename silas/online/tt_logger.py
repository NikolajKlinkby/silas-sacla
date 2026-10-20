"""Online time-tool log (beamline, olpy): the edge of every new tag, written run by run while
the runs are taken. The same files as the HPC logger (silas.offline.tt_logger):
<output>/timetool/edges/run_<run>.csv and index.csv.

    start_tt_logger()                                       # from the session (new terminal, MPI)
    mpirun -n 3 python -m silas.online.tt_logger         # 1 writer + 2 readers
    python -m silas.online.tt_logger --test              # the test camera, one process, no SACLA

Readers (MPI ranks 1..N; without MPI the one process): reader k takes the shots with
(tag / tag step) mod N == k from the online storage, finds the edge of each
(silas.core.timetool.shot_edge, the current settings and background) and sends the rows
to rank 0 every second. Rank 0 (the writer) gives every tag its run (the newest run whose
start tag is <= the tag, from the SyncDB), appends the rows to the run's file every couple of
seconds and, once a run is stopped and no new tags come for it, sorts its file and notes it in
index.csv (status "online", the number of shots missed). The shot class is not known online
(the flags reach the SyncDB later): cls = -1; the analysis takes the flags from the SyncDB.
Shots missed online (the buffer moved on) can be filled in on the HPC afterwards with the
offline logger (make_tt_log), which does the whole run again.
"""
import argparse
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import sacla, timetool
from silas.core.config import Config
from silas.online.tt_source import TestCamera, detect_tag_step, open_camera

TAG_DATA = 41
TAG_STOP = 42
WRITE_S = 2.0          # writer: append to the files this often
QUIET_S = 10.0         # a stopped run with no new tags for this long is finished


class EdgeReader(object):
    """Live: the edge of this worker's newest shots."""

    def __init__(self, src: Any, cfg: Config, wid: int = 0, nworkers: int = 1, tag_step: int = 1) -> None:
        self.src = src
        self.cfg = cfg
        self.stamp = None  # type: Any
        self.s = {}  # type: Dict[str, Any]
        self.sid = ""
        self.bg = timetool.Background()
        self.bt_key = None  # type: Any
        self.bt = None  # type: Optional[np.ndarray]
        self.wid = wid
        self.nw = nworkers
        self.step_ = max(1, int(tag_step))
        self.last_tag = None  # type: Optional[int]
        self.rows = []  # type: List[Dict[str, Any]]
        self.missed = 0
        self.t_work = 0.0
        self.refresh()

    def refresh(self) -> None:
        stamp = self.cfg.file_stamp()
        if stamp == self.stamp:
            return
        self.stamp = stamp
        try:
            self.cfg.reload()
            self.s = timetool.settings(self.cfg)
            self.sid = timetool.settings_id(self.s)
            self.bg.load(self.s["background"].get("file"))
            self.bt_key = None
        except (ValueError, KeyError) as err:
            print("config not reloaded:", err)

    def step(self) -> bool:
        step = self.step_
        newest = int(self.src.newest_tag())
        slot = (newest // step) % self.nw
        target = newest - ((slot - self.wid) % self.nw) * step
        if self.last_tag is not None and target <= self.last_tag:
            return False
        if self.last_tag is not None:
            self.missed += max(0, (target - self.last_tag) // (step * self.nw) - 1)
        self.last_tag = target
        t0 = time.perf_counter()
        img = self.src.read(target)
        if img is None:
            self.missed += 1
            return True
        key = (img.shape, self.s.get("roi"), self.s.get("binning"), self.bg.path)
        if key != self.bt_key:
            self.bt = self.bg.trace(self.s.get("roi"), self.s.get("binning", "rows"), img.shape)
            self.bt_key = key
        e = timetool.shot_edge(img, self.s, self.bt)
        row = {k: e[k] for k in ("edge", "edge_deriv", "edge_fit", "step", "width", "snr")}  # type: Dict[str, Any]
        row.update(tag=int(target), good=bool(e["good"]), cls=-1, sid=self.sid)
        self.rows.append(row)
        self.t_work += time.perf_counter() - t0
        return True

    def take(self) -> Dict[str, Any]:
        msg = {"rows": self.rows, "missed": self.missed, "t_work": self.t_work}
        self.rows, self.missed, self.t_work = [], 0, 0.0
        return msg


class RunMap(object):
    """tag -> run, from the runs' start tags (SyncDB), or the test camera's runs."""

    def __init__(self, cfg: Config, src: Any) -> None:
        self.cfg = cfg
        self.src = src
        self.test = isinstance(src, TestCamera)
        self.starts = {}  # type: Dict[int, int]
        self.status = {}  # type: Dict[int, int]
        self.next_check = 0.0

    def update(self) -> None:
        if self.test or time.time() < self.next_check:
            return
        from silas.core import db

        self.next_check = time.time() + 2.0
        bl = self.cfg.beamline
        try:
            newest = db.newest_run(bl)
            first = self.cfg.get("first_run", None)
            look = range(max(newest - 3, int(first) if first else newest - 3), newest + 1)
            for run in look:
                st = db.run_status(bl, run)
                if st < 0:
                    continue
                self.status[run] = st
                if run not in self.starts:
                    self.starts[run] = int(db.tag_range(bl, run)[0])
            for run in list(self.status):          # runs still open: are they stopped now?
                if self.status[run] != 0 and run not in look:
                    self.status[run] = db.run_status(bl, run)
        except db.api_error() as err:
            print("run list not readable now ({}); trying again".format(err.args))

    def run_of(self, tag: int) -> Optional[int]:
        if self.test:
            return int(self.src.run_of(tag))
        best = None  # type: Optional[int]
        for run, start in self.starts.items():
            if start <= tag and (best is None or run > best):
                best = run
        return best

    def stopped(self, run: int) -> bool:
        if self.test:
            return bool(self.src.run_stopped(run))
        if run not in self.status:                 # e.g. a run left over from before a restart
            from silas.core import db

            try:
                self.status[run] = int(db.run_status(self.cfg.beamline, run))
            except db.api_error():
                return False
        return self.status.get(run, 2) == 0


class Writer(object):
    """Rank 0: rows -> run files; finished runs -> index.csv."""

    def __init__(self, cfg: Config, runmap: RunMap) -> None:
        self.cfg = cfg
        self.runmap = runmap
        self.pending = {}  # type: Dict[int, List[Dict[str, Any]]]
        self.active = {}  # type: Dict[int, Dict[str, Any]]
        self.unknown = 0
        self.next_write = time.time() + WRITE_S
        self.next_report = time.time() + 10.0
        self.received = []  # type: List[Tuple[float, int]]
        self.missed_total = 0
        self.t_work = 0.0
        self.n_work = 0
        self.resume()

    def resume(self) -> None:
        """Runs with a file but not in index.csv (the logger stopped before they were finished):
        finished as soon as they are stopped; new tags of the current run are appended."""
        from silas.offline import tt_logger as off

        idx = off.load_index(self.cfg)
        folder = timetool.edges_dir(self.cfg)
        for name in sorted(os.listdir(folder)):
            if not (name.startswith("run_") and name.endswith(".csv")):
                continue
            try:
                run = int(name[4:-4])
            except ValueError:
                continue
            if run in idx.index:
                continue
            df = timetool.read_edges_csv(os.path.join(folder, name))
            self.active[run] = {"edges": int(len(df)), "good": int(df["good"].astype(str).isin(["True", "1"]).sum())
                                if len(df) else 0, "missed": 0, "last": 0.0}
            print("continuing run {}: {} edges from before".format(run, len(df)), flush=True)

    def add(self, msg: Dict[str, Any]) -> None:
        self.runmap.update()
        for row in msg.get("rows", []):
            run = self.runmap.run_of(int(row["tag"]))
            if run is None:
                self.unknown += 1
                continue
            self.pending.setdefault(run, []).append(row)
            a = self.active.setdefault(run, {"edges": 0, "good": 0, "missed": 0, "last": time.time()})
            a["edges"] += 1
            a["good"] += int(bool(row["good"]))
            a["last"] = time.time()
        n = len(msg.get("rows", []))
        self.received.append((time.time(), n))
        self.missed_total += int(msg.get("missed", 0))
        if self.active and msg.get("missed"):
            newest = max(self.active)
            self.active[newest]["missed"] += int(msg["missed"])
        self.t_work += float(msg.get("t_work", 0.0))
        self.n_work += n

    def rate(self) -> float:
        now = time.time()
        self.received = [(t, n) for t, n in self.received if now - t < 10.0]
        if len(self.received) < 2:
            return 0.0
        return sum(n for _, n in self.received[1:]) / max(self.received[-1][0] - self.received[0][0], 1e-3)

    def tick(self) -> None:
        self.runmap.update()
        now = time.time()
        if now >= self.next_write:
            self.flush()
            self.next_write = now + WRITE_S
        for run in sorted(self.active):
            a = self.active[run]
            if self.runmap.stopped(run) and now - a["last"] > QUIET_S and not self.pending.get(run):
                self.finish(run)
        if now >= self.next_report:
            self.next_report = now + 10.0
            parts = ["run {}: {} edges ({} good), missed {}".format(r, a["edges"], a["good"], a["missed"])
                     for r, a in sorted(self.active.items())[-2:]]
            print("{:.1f} shots/s, {:.1f} ms per shot{}   {}".format(
                self.rate(), 1000 * self.t_work / max(self.n_work, 1),
                ", {} tags of no known run".format(self.unknown) if self.unknown else "",
                "; ".join(parts) or "waiting for shots"), flush=True)

    def flush(self) -> None:
        for run, rows in list(self.pending.items()):
            if not rows:
                continue
            path = timetool.edges_path(self.cfg, run)
            new = not os.path.exists(path)
            with open(path, "a") as fh:
                if new:
                    fh.write(",".join(timetool.EDGE_COLUMNS) + "\n")
                for r in rows:
                    fh.write(",".join(_fmt(r.get(c)) for c in timetool.EDGE_COLUMNS) + "\n")
            self.pending[run] = []

    def finish(self, run: int) -> None:
        """Sort the run's file, note it in the index."""
        from silas.offline import tt_logger as off

        self.flush()
        a = self.active.pop(run)
        df = timetool.load_edges(self.cfg, run, current_only=False)
        off.write_run(self.cfg, run, df.to_dict("records"))
        idx = off.load_index(self.cfg)
        sid = str(df["sid"].iloc[-1]) if len(df) else ""
        for c, v in (("status", "online"), ("tags", len(df)), ("edges", len(df)), ("good", int(df["good"].sum())),
                     ("timing", np.nan), ("timing_good", np.nan), ("sid", sid), ("camera", ""),
                     ("seconds", np.nan), ("written", off.now())):
            idx.loc[run, c] = v
        off.save_index(idx, off.index_path(self.cfg))
        print("run {} finished: {} edges, {} good, {} shots missed -> {}".format(
            run, a["edges"], a["good"], a["missed"], timetool.edges_path(self.cfg, run)), flush=True)


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (bool, np.bool_)):
        return "True" if v else "False"
    if isinstance(v, float):
        return "" if v != v else "{:.4f}".format(v)
    return str(v)


def run(args: Any) -> None:
    from silas.core import mpi
    from silas.core.runlog import _Lock
    from silas.offline.tt_logger import index_path

    comm, rank, size, MPI = mpi.world()
    cfg = Config()
    s = timetool.settings(cfg)
    src = open_camera(cfg, test=args.test, det_id=args.camera)
    step = detect_tag_step(src)
    if rank > 0:  # ------------------------------------------- reader
        reader = EdgeReader(src, cfg, wid=rank - 1, nworkers=size - 1, tag_step=step)
        next_send = time.time() + 1.0
        while True:
            if comm.Iprobe(source=0, tag=TAG_STOP):
                comm.recv(source=0, tag=TAG_STOP)
                break
            reader.refresh()
            busy = reader.step()
            if time.time() >= next_send:
                comm.send(reader.take(), dest=0, tag=TAG_DATA)
                next_send = time.time() + 1.0
            if not busy:
                time.sleep(0.002)
        return
    # ----------------------------------------------------------- writer (rank 0)
    if not s["background"].get("file"):
        print("note: no time-tool background (capture_tt_background()): no edge will be good")
    lock = _Lock(index_path(cfg))
    lock.acquire()
    writer = Writer(cfg, RunMap(cfg, src))
    local = EdgeReader(src, cfg, tag_step=step) if size == 1 else None
    print("online time-tool log: {} (tag step {}), {}; files in {}. Ctrl-C stops.".format(
        src.label, step, "{} MPI readers".format(size - 1) if size > 1 else "one process", timetool.edges_dir(cfg)),
        flush=True)
    try:
        while True:
            if local is not None:
                t_end = time.time() + 1.0
                while time.time() < t_end:
                    local.refresh()
                    if not local.step():
                        time.sleep(0.002)
                writer.add(local.take())
            else:
                while comm.Iprobe(tag=TAG_DATA):
                    writer.add(comm.recv(tag=TAG_DATA))
                time.sleep(0.05)
            writer.tick()
            lock.touch()
    except KeyboardInterrupt:
        print("\nstopping: writing what is there ...")
    finally:
        writer.flush()
        if comm is not None and size > 1:
            for r in range(1, size):
                comm.send(None, dest=r, tag=TAG_STOP)
        lock.release()
        print("online time-tool log stopped; files in {}".format(timetool.edges_dir(cfg)))


@sacla.requires_sacla
def run_live(args: Any) -> None:
    run(args)


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="online time-tool log: the edge of every new tag (beamline, olpy)")
    ap.add_argument("--test", action="store_true", help="the test camera (no SACLA library)")
    ap.add_argument("--camera", help="camera detector ID (default: the config's, else the first OPAL / IPX / CHET)")
    args = ap.parse_args(argv)
    if args.test:
        run(args)
    else:
        run_live(args)


if __name__ == "__main__":
    main()
