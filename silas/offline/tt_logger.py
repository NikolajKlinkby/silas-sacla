"""Time-tool log (HPC, stpy): the edge of EVERY tag of every run, run by run, like the run logger.

    make_tt_log()                                          # from the session: up to the newest run, then stop
    make_tt_log(last_run=1234551)                           # ... up to run 1234551
    make_tt_log(follow=True)                               # keep following new runs (Ctrl-C stops)
    python -m silas.offline.tt_logger [--follow] [--last-run N] [--first-run N] [--rebuild] [--workers 6]
    qsub -v LAST_RUN=1234551 ~/SILAS/scripts/tt_logger_job.sh      # batch job (follows without last_run)

Written to <output>/timetool/edges/:
  run_<run>.csv   one row per tag: tag, edge (camera pixel), edge_deriv, edge_fit, step, width,
                  snr, good, cls (shot class: 0 timing, 1 background, 2 X-rays only, 3 other,
                  -1 unknown), sid (id of the settings it was made with: ROI, binning,
                  background, edge settings; silas.core.timetool.settings_id)
  index.csv       one row per run: status, tags, edges, good edges, timing shots, sid, camera,
                  seconds, written
The correction of each shot (pulse, ps) is NOT stored: timetool.load_edges(cfg, run) (session:
load_tt(run)) adds it from the CURRENT calibration, so a new calibration needs no new log.

Rules (as the run logger): starts at the config's first_run (or --first-run); a run is done
when it is stopped (finished) and all its tags have an edge; runs that are still running are
done on a later pass; runs without the time-tool camera are noted and skipped. Starting again
(after a crash, Ctrl-C, the end of a batch job) simply continues: runs in index.csv are not done
again, only --rebuild starts from scratch. Runs logged with other settings (another sid) are
kept unless --redo-changed (the scan analysis redoes the runs it needs). Every run is split into parts of about 500 tags,
done by a pool of --workers processes (one line of progress per part); each run's file is
written as soon as its parts are done. A lock file (index.csv.lock, with a heartbeat) stops
two loggers writing the same log; tt_logger_status() tells whether one is running.
--follow: after catching up, look for new runs every --poll seconds; --last-run N: stop after
run N (also with --follow: it waits for run N to finish).
"""
import argparse
import datetime
import os
import time
from typing import Any, Dict, List, Optional

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import db, runner, sacla, timetool
from silas.core.config import Config
from silas.core.runlog import JST, _Lock
from silas.offline.tt_calibration import make_jobs, map_jobs

INDEX_COLUMNS = ["run", "status", "tags", "edges", "good", "timing", "timing_good", "sid", "camera", "seconds",
                 "written"]
BATCH = 20           # runs per round of the pool (each run's file is written when it is done)


def index_path(cfg: Any) -> str:
    return os.path.join(timetool.edges_dir(cfg), "index.csv")


def load_index(cfg: Any) -> Any:
    import pandas as pd

    path = index_path(cfg)
    if os.path.exists(path):
        df = pd.read_csv(path, dtype={"sid": str, "status": str, "camera": str})
        if len(df):
            return df.set_index("run")
    return pd.DataFrame(columns=INDEX_COLUMNS).set_index("run")


def save_index(df: Any, path: str) -> None:
    tmp = path + ".tmp"
    df.sort_index().to_csv(tmp)
    os.replace(tmp, path)


def write_run(cfg: Any, run: int, rows: List[Dict[str, Any]]) -> str:
    """The run's file (sorted by tag, atomically)."""
    import pandas as pd

    path = timetool.edges_path(cfg, run)
    df = pd.DataFrame(rows)
    for c in timetool.EDGE_COLUMNS:
        if c not in df:
            df[c] = np.nan
    df = df[timetool.EDGE_COLUMNS].sort_values("tag")
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False, float_format="%.4f")
    os.replace(tmp, path)
    return path


def now() -> str:
    return datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


class EdgeLogger(object):
    """Keeps <output>/timetool/edges/ up to date (HPC, stpy)."""

    def __init__(self, cfg: Config, workers: int = runner.OFFLINE_WORKERS, redo_changed: bool = False) -> None:
        self.cfg = cfg
        self.redo_changed = redo_changed
        self.told_changed = False
        self.bl = cfg.beamline
        self.workers = workers
        self.index = load_index(cfg)
        self.path = index_path(cfg)
        self.heartbeat = lambda: None  # type: Any
        self.skip = set()  # type: set
        """runs that are not runs of this beamline (asked once)"""

    def _note(self, run: int, **fields: Any) -> None:
        row = {c: (self.index.at[run, c] if run in self.index.index and c in self.index.columns else np.nan)
               for c in INDEX_COLUMNS if c != "run"}
        row.update(fields)
        row["written"] = now()
        for c, v in row.items():
            self.index.loc[run, c] = v
        save_index(self.index, self.path)

    def _status(self, run: int) -> int:
        try:
            return int(db.run_status(self.bl, run))
        except db.api_error():
            return -1

    def todo(self, s: Dict[str, Any], first: int, last: int) -> List[int]:
        """Runs from first to last that need doing: stopped, with the camera, not done with
        these settings. Runs not yet stopped are left for a later pass."""
        sid = timetool.settings_id(s)
        out = []  # type: List[int]
        changed = []  # type: List[int]
        for run in range(first, last + 1):
            if run in self.skip:
                continue
            if run in self.index.index:
                st = str(self.index.at[run, "status"])
                if st == "no camera" or (st == "done" and str(self.index.at[run, "sid"]) == sid):
                    continue
                if st == "done" and not self.redo_changed:
                    changed.append(run)            # done with other settings: kept unless asked
                    continue
            status = self._status(run)
            if status < 0:
                if run < last:
                    self.skip.add(run)                    # not a run of this beamline
                continue
            if status != 0:
                continue                                  # still running: later
            try:
                dets = list(db.run_detectors(self.bl, run))
            except db.api_error():
                dets = []
            cam = s.get("detector") or timetool.find_camera(dets)
            if not cam or cam not in dets:
                self._note(run, status="no camera", camera="", sid="")
                print("run {}: no time-tool camera recorded ({}): skipped".format(run, ", ".join(dets) or "no detectors"))
                continue
            out.append(run)
        if changed and not self.told_changed:
            self.told_changed = True
            print("{} runs ({} .. {}) were logged with other time-tool settings; they are kept. To do them "
                  "again with the current settings: make_tt_log(redo_changed=True) (--redo-changed); the scan "
                  "analysis does it for the runs it needs.".format(len(changed), changed[0], changed[-1]))
        return out

    def process(self, runs: List[int], s: Dict[str, Any]) -> None:
        """The edges of every tag of these runs, the pool working on parts of runs; each run's
        file written as soon as its parts are done."""
        sid = timetool.settings_id(s)
        for i in range(0, len(runs), BATCH):
            batch = runs[i:i + BATCH]
            jobs = make_jobs(self.cfg.path, batch, None, None, s, None, all_shots=True)
            parts = {r: [] for r in batch}  # type: Dict[int, List[Dict[str, Any]]]

            def done(res: Dict[str, Any]) -> None:
                parts[res["run"]].append(res)
                self.heartbeat()
                got = parts[res["run"]]
                if len(got) < res["n_parts"]:
                    return
                rows = [row for p in got for row in p["rows"]]
                write_run(self.cfg, res["run"], rows)
                good = sum(1 for r in rows if r["good"])
                timing = [r for r in rows if r["cls"] == timetool.TIMING]
                self._note(res["run"], status="done", tags=sum(p["n_tags"] for p in got), edges=len(rows), good=good,
                           timing=len(timing), timing_good=sum(1 for r in timing if r["good"]), sid=sid,
                           camera=got[0]["det_id"], seconds=round(sum(p["seconds"] for p in got), 1))
                print("run {}: {} tags, {} timing shots, {} good edges -> {}".format(
                    res["run"], len(rows), len(timing), sum(1 for r in timing if r["good"]),
                    timetool.edges_path(self.cfg, res["run"])), flush=True)

            map_jobs(jobs, self.workers, on_result=done)

    def update_once(self, first: int, last_run: Optional[int]) -> int:
        """One pass. Returns the newest run looked at."""
        self.cfg.reload()
        s = timetool.settings(self.cfg)
        newest = db.newest_run(self.bl)
        last = min(newest, last_run) if last_run is not None else newest
        runs = self.todo(s, first, last)
        if runs:
            print("{} runs to do ({} .. {}), settings {}".format(len(runs), runs[0], runs[-1], timetool.settings_id(s)),
                  flush=True)
            self.process(runs, s)
        self.heartbeat()
        return last

    def finished(self, last_run: int) -> bool:
        """Run last_run is done (or has no camera)."""
        return last_run in self.index.index and str(self.index.at[last_run, "status"]) in ("done", "no camera")

    def run(self, follow: bool = False, poll: float = 30.0, rebuild: bool = False, first_run: Optional[int] = None,
            last_run: Optional[int] = None) -> None:
        s = timetool.settings(self.cfg)
        if not s["background"].get("file"):
            print("no time-tool background: set_tt_background(run=...) first (without it no edge is good)")
            return
        first = first_run if first_run is not None else self.cfg.get("first_run", None)
        last = last_run or self.cfg.get("last_run", None)
        if first is None:
            raise ValueError('no first run: cfg.set("first_run", <run number>) or --first-run')
        lock = _Lock(self.path)
        lock.acquire()
        self.heartbeat = lock.touch
        try:
            if rebuild:
                import pandas as pd

                self.index = pd.DataFrame(columns=INDEX_COLUMNS).set_index("run")
                save_index(self.index, self.path)
            print("time-tool log: {}  (beamline {}, from run {}{}, {})".format(
                timetool.edges_dir(self.cfg), self.bl, first, ", stop at run {}".format(last_run) if last_run else "",
                "following new runs, Ctrl-C to stop" if follow else "one pass"))
            print("settings: ROI {}, binning {}, background {} ({}), edge: {}".format(
                s.get("roi") or "whole image", s.get("binning"), s["background"].get("source"),
                s["background"].get("mode"), timetool.edge_text(s["edge"])), flush=True)
            self.update_once(int(first), last_run)
            while follow and not (last_run is not None and self.finished(last_run)):
                time.sleep(poll)
                self.update_once(int(first), last_run)
            print("time-tool log up to date{}".format(" (stopped at run {})".format(last_run) if last_run else ""))
        except KeyboardInterrupt:
            print("\nstopped; the runs done so far are in {}".format(timetool.edges_dir(self.cfg)))
        finally:
            lock.release()


def status(cfg: Any, max_age_s: float = 120.0) -> str:
    """Is a time-tool logger running (lock file and heartbeat), and what is in the log."""
    import socket

    from silas.core.runlog import _alive

    lock = index_path(cfg) + ".lock"
    idx = load_index(cfg)
    done = idx[idx["status"].astype(str).isin(["done", "online"])] if len(idx) else idx
    text = "time-tool log {}: {} runs done{}".format(
        timetool.edges_dir(cfg), len(done), " ({} .. {})".format(done.index.min(), done.index.max()) if len(done) else "")
    if not os.path.exists(lock):
        return text + "; no logger running"
    with open(lock) as fh:
        owner = fh.read().strip()
    age = time.time() - os.path.getmtime(lock)
    host, _, pid = owner.partition(":")
    if host == socket.gethostname() and not (pid.isdigit() and _alive(int(pid))):
        return text + "; stale lock (process {} is gone): {}".format(pid, lock)
    return text + "; logger {} ({}, last sign of life {:.0f} s ago)".format(
        owner, "running" if age < max_age_s else "not updated, hanging?", age)


@sacla.requires_sacla
def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="time-tool log: the edge of every tag of every run (HPC, stpy)")
    ap.add_argument("--follow", action="store_true", help="keep following new runs (Ctrl-C stops)")
    ap.add_argument("--last-run", "--last_run", type=int, dest="last_run", help="stop after this run")
    ap.add_argument("--first-run", type=int, help="start here instead of the config's first_run")
    ap.add_argument("--rebuild", action="store_true", help="forget the index and do every run again")
    ap.add_argument("--redo-changed", action="store_true",
                    help="also do again the runs logged with other settings (default: keep them)")
    ap.add_argument("--poll", type=float, default=30.0, help="seconds between looks for new runs (default 30)")
    ap.add_argument("--workers", type=int, default=runner.OFFLINE_WORKERS,
                    help="processes (default {})".format(runner.OFFLINE_WORKERS))
    args = ap.parse_args(argv)
    EdgeLogger(Config(), args.workers, redo_changed=args.redo_changed).run(follow=args.follow, poll=args.poll, rebuild=args.rebuild,
                                           first_run=args.first_run, last_run=args.last_run)


if __name__ == "__main__":
    main()
