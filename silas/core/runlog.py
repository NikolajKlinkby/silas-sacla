"""Run log: one row per run of the beamline, written to <output>/runlog.csv.

Shared by the online logger (follows new runs forever) and the offline logger
(catches up and stops). Only dbpy is used, which works on both networks.

Next to the run log the logger writes
  <output>/motors.csv   motor snapshot of every run (all BL motors only until the
                        scanned motor is known, see silas.core.scan)
  <output>/scans.csv    one row per scan: its runs, the scanned motor, steps, ...
(see silas.core.scan). The run log gets the scan columns of each run.

Rules
  * starts at the config's "first_run" (the start of the beamtime), or after
    the last run already in the file
  * a run is written as soon as it exists, also while running/paused; such rows
    are re-checked on every pass and updated until the run is stopped
  * run numbers up to dbpy.read_runnumber_newest(bl) that do not exist for this
    beamline are skipped
  * the file is always rewritten atomically; a lock file stops two loggers
    writing the same log. The logger touches the lock file on every pass (heartbeat),
    so logger_status() can tell a running logger from a stale lock.

Parallel (offline, workers > 1; the online logger reads one run after the other): a pass
first reads the run records of all runs with the workers, then the motor snapshots with
whole scans per worker (inside a scan in run order, because step 1 searches all motors and
the later steps use what it found), in batches of about CHECKPOINT runs; the log is saved
after every batch.
"""
import atexit
import datetime
import math
import os
import socket
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from silas.core import db, names, scan
from silas.core.config import Config

JST = datetime.timezone(datetime.timedelta(hours=9), "JST")

COLUMNS = [
    "run", "status", "start_jst", "stop_jst", "duration_s", "total_tagnumber",
    "scan_id", "step", "total_steps", "scan_motor", "scan_value",
    "comment", "runtype", "scanmode", "moving", "detectors", "start_tagnumber", "end_tagnumber",
    "hightagnumber", "stationnumber", "fy_id", "runstatus", "starttime", "stoptime",
    "logged_at",
]

NUMERIC = ("duration_s", "total_tagnumber", "scan_id", "step", "total_steps", "scan_value", "start_tagnumber", "end_tagnumber", "hightagnumber",
           "stationnumber", "runstatus", "starttime", "stoptime")
"""Columns stored as numbers (empty cell = NaN); all others are text."""

_INFO_KEYS = ("starttime", "stoptime", "total_tagnumber", "start_tagnumber", "end_tagnumber",
              "hightagnumber", "comment", "runtype", "stationnumber", "fy_id", "runstatus")


def _jst(t: Any) -> str:
    try:
        if not t:
            return ""
        return datetime.datetime.fromtimestamp(float(t), JST).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def runlog_path(cfg: Config) -> str:
    return os.path.join(cfg.output_dir(), "runlog.csv")


# ---------------------------------------------------------------- file
def empty() -> Any:
    """An empty run log table."""
    import pandas as pd

    return pd.DataFrame(columns=COLUMNS).set_index("run")


def load(path: str) -> Any:
    """The run log as a pandas DataFrame indexed by run (empty if no file yet)."""
    import pandas as pd

    if not os.path.exists(path):
        return empty()
    df = pd.read_csv(path, index_col="run", dtype=str, keep_default_na=False)
    df.index = df.index.astype(int)
    df = df.reindex(columns=COLUMNS[1:], fill_value="")
    for col in NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def save(df: Any, path: str) -> None:
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    tmp = path + ".tmp"
    df.sort_index().to_csv(tmp)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o664)  # group members may read/update the log too
    except OSError:
        pass


# ---------------------------------------------------------------- one run
def record(bl: int, run: int) -> Optional[Dict[str, Any]]:
    """Row for ``run`` or None if the run does not exist (yet) on this beamline."""
    try:
        status = db.run_status(bl, run)
    except db.api_error():
        return None
    if status < 0:
        return None
    info = db.run_info(bl, run)
    row = {k: info.get(k, "") for k in _INFO_KEYS}  # type: Dict[str, Any]
    try:
        row["detectors"] = ";".join(db.run_detectors(bl, run))
    except db.api_error():
        row["detectors"] = str(info.get("detectors", ""))
    row["run"] = run
    row["runstatus"] = status
    row["status"] = db.RUN_STATUS.get(status, str(status))
    row["start_jst"] = _jst(info.get("starttime"))
    row["stop_jst"] = _jst(info.get("stoptime")) if status == 0 else ""
    try:
        row["duration_s"] = round(float(info["stoptime"]) - float(info["starttime"]), 1) if status == 0 else math.nan
    except (KeyError, TypeError, ValueError):
        row["duration_s"] = math.nan
    st = scan.step_info(bl, run)
    row["step"] = st.get("stepnumber", math.nan)
    row["total_steps"] = st.get("total_stepnumber", math.nan)
    row["scanmode"] = st.get("scanmode", "")
    row["logged_at"] = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    return row


def _print_row(row: Dict[str, Any]) -> None:
    step = ""
    if not (isinstance(row.get("step"), float) and math.isnan(row["step"])):
        step = "step {}/{}".format(row.get("step"), row.get("total_steps"))
    comment = str(row.get("comment", "")).replace("\n", " | ")
    print("run {run:<9} {status:<8} {start_jst:<19}  tags={total_tagnumber!s:<7} {step:<12} {comment}".format(
        step=step, comment=comment[:80], **{k: v for k, v in row.items() if k not in ("step", "comment")}))


# ---------------------------------------------------------------- lock
STALE_S = 600.0
"""A lock from ANOTHER computer whose heartbeat is older than this is taken over (a crashed batch
job on another node); on the same computer the process itself is checked."""


class _Lock(object):
    """<log>.lock with host:pid; refuses if another live logger holds it. A lock of a process that
    is gone (same computer), or from another computer without a heartbeat for STALE_S seconds, is
    taken over: a new logger then simply continues where the old one stopped."""

    def __init__(self, path: str) -> None:
        self.path = path + ".lock"
        self.me = "{}:{}".format(socket.gethostname(), os.getpid())

    def acquire(self) -> None:
        if os.path.exists(self.path):
            with open(self.path) as fh:
                owner = fh.read().strip()
            host, _, pid = owner.partition(":")
            if host == socket.gethostname() and pid.isdigit() and _alive(int(pid)):
                raise RuntimeError("another logger is writing this log ({}); lock file {}".format(owner, self.path))
            if host != socket.gethostname():
                try:
                    age = time.time() - os.path.getmtime(self.path)
                except OSError:
                    age = 0.0
                if age < STALE_S:
                    raise RuntimeError("log is locked by {} (other host, alive {:.0f} s ago). Delete {} if that "
                                       "logger is no longer running.".format(owner, age, self.path))
                print("taking over the lock of {} (no sign of life for {:.0f} s)".format(owner, age))
            else:
                print("taking over the lock of {} (that process is gone)".format(owner))
        with open(self.path, "w") as fh:
            fh.write(self.me + "\n")
        atexit.register(self.release)

    def touch(self) -> None:
        """Heartbeat: update the lock file's time (a logger is alive while it is recent)."""
        try:
            os.utime(self.path, None)
        except OSError:
            pass

    def release(self) -> None:
        try:
            with open(self.path) as fh:
                if fh.read().strip() == self.me:
                    os.remove(self.path)
        except OSError:
            pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def logger_status(cfg: Config, max_age_s: float = 60.0) -> Tuple[bool, str]:
    """(running?, text) for the run logger of this profile's output folder.

    Running: the lock file exists, its process is alive (same computer) and its heartbeat
    is younger than max_age_s (the online logger touches it every --poll seconds).
    A lock from another computer counts as running while its heartbeat is recent."""
    path = runlog_path(cfg) + ".lock"
    if not os.path.exists(path):
        return False, "no run logger running (no lock file {})".format(path)
    try:
        with open(path) as fh:
            owner = fh.read().strip()
        age = time.time() - os.path.getmtime(path)
    except OSError as err:
        return False, "lock file not readable: {}".format(err)
    host, _, pid = owner.partition(":")
    if host == socket.gethostname():
        if not (pid.isdigit() and _alive(int(pid))):
            return False, "stale lock file (process {} is gone): {}".format(pid, path)
        if age > max_age_s:
            return False, "run logger {} has not updated for {:.0f} s (hanging?)".format(owner, age)
        return True, "run logger running ({}, last pass {:.0f} s ago)".format(owner, age)
    if age > max_age_s:
        return False, "lock from {} not updated for {:.0f} s".format(owner, age)
    return True, "run logger running on {} (last pass {:.0f} s ago)".format(host, age)


# ---------------------------------------------------------------- logger
class RunLogger(object):
    """Keeps <output>/runlog.csv up to date for the config's beamline."""

    def __init__(self, cfg: Config, path: Optional[str] = None) -> None:
        self.cfg = cfg
        self.bl = cfg.beamline
        self.path = path or runlog_path(cfg)
        self.df = load(self.path)
        self.motors_path = os.path.join(os.path.dirname(self.path), "motors.csv")
        self.scans_path = os.path.join(os.path.dirname(self.path), "scans.csv")
        self.motors = scan.load_motors(self.motors_path)
        self.checked_up_to = None  # type: Optional[int]
        self._candidates = None  # type: Optional[List[str]]
        self.heartbeat = None  # type: Any
        # called after every run read (the lock's touch: a long first pass is not "hanging")
        self.workers = 1

    def candidates(self) -> List[str]:
        """Motors in the snapshot of every run (read once per logger)."""
        if self._candidates is None:
            try:
                equip = db.equipment() if self.cfg.get("scan.search", "all") == "all" else ()
            except db.api_error() as err:
                print("equipment list not readable ({}): only the configured motors are checked".format(err))
                equip = ()
            self._candidates = scan.candidate_motors(self.cfg, equip)
            print("scan detection: {} motors searched at the start of a scan ({} configured)".format(
                len(self._candidates), len(scan.motor_names(self.cfg))))
        return self._candidates

    def motors_for(self, run: int) -> Tuple[List[str], bool]:
        """(motors to read for ``run``, full search?). The run's row must be in self.df.
        Plain run: configured motors. Scan with known motor: configured + scanned motors.
        Otherwise: step 1 (no earlier run in the scan) all BL motors, later steps the motors
        that had data in the scan's first (full) snapshot."""
        configured = scan.motor_names(self.cfg)
        if scan._num(self.df.at[run, "step"]) is None:
            return configured, False
        group = next((g for g in scan.group_runs(self.df) if run in g), [run])
        earlier = [r for r in group if r != run and r in self.motors]
        if earlier:
            _, scanned = scan.scan_row(self.cfg, self.df, earlier, self.motors)
            if scanned:
                return configured + [m for m in scanned if m not in configured], False
            first = self.motors[earlier[0]]
            if first.get(scan.FULL):
                keys = scan.motor_keys(first)
                return configured + [m for m in keys if m not in configured], False
        own = self.motors.get(run, {})
        if own.get(scan.FULL):  # step 1 again when it has stopped: the motors found then
            return configured + [m for m in scan.motor_keys(own) if m not in configured], True
        return self.candidates(), True

    def _snapshot(self, run: int) -> Dict[str, Any]:
        equip, full = self.motors_for(run)
        try:
            snap = scan.snapshot(self.bl, run, equip, int(self.cfg.get("scan.n_sample", 5)))
        except db.api_error() as err:
            print("run {}: no motor snapshot ({})".format(run, err))
            snap = {scan.MOVING: ""}
        snap[scan.FULL] = 1 if full else 0
        return snap

    def update_scans(self, changed: List[int]) -> None:
        """Rebuild the scan table, fill the scan columns of the run log, print the scans of
        the changed runs."""
        scans, per_run = scan.build_scans(self.cfg, self.df, self.motors)
        for col in ("scan_id", "scan_motor", "scan_value"):
            self.df[col] = [per_run.get(int(r), {}).get(col, math.nan if col != "scan_motor" else "")
                            for r in self.df.index]
        save(scans, self.scans_path)
        for sid in sorted({int(per_run[r]["scan_id"]) for r in changed if r in per_run}):
            print("    " + scan.describe(scans.loc[sid], sid))

    def _start_run(self, first_run: Optional[int]) -> int:
        first = first_run if first_run is not None else self.cfg.get("first_run", None)
        if first is None:
            raise ValueError('no first run: cfg.set("first_run", <run number>) or pass first_run')
        if len(self.df):
            return max(int(first), int(self.df.index.max()) + 1)
        return int(first)

    def update_once(self, first_run: Optional[int] = None, last_run: Optional[int] = None) -> List[int]:
        """One pass: re-check unfinished runs, add new ones. Returns changed runs."""
        newest = db.newest_run(self.bl)
        if last_run is not None:
            newest = min(newest, last_run)
        start = self.checked_up_to + 1 if self.checked_up_to is not None else self._start_run(first_run)

        import pandas as pd

        status = pd.to_numeric(self.df["runstatus"], errors="coerce")
        pending = [int(r) for r in self.df.index[status != 0]]
        candidates = sorted(set(pending) | set(range(start, newest + 1)))
        # runs logged before motor snapshots existed are recorded once more
        candidates = sorted(set(candidates) | {int(r) for r in self.df.index if int(r) not in self.motors})
        changed = []  # type: List[int]
        try:
            self._pass(candidates, changed)
        finally:  # also on Ctrl-C / errors: keep what was read (a new start resumes after it)
            if changed:
                self.update_scans(changed)
                scan.save_motors(self.motors, self.motors_path)
                save(self.df, self.path)
        self.checked_up_to = max(newest, start - 1)
        return changed

    CHECKPOINT = 20
    """Save the log every this many new / changed runs during a long pass."""

    def _apply(self, run: int, row: Dict[str, Any]) -> Optional[bool]:
        """Put a run's record into the log. None: nothing to do (stopped and logged before);
        True: its motor snapshot has to be (re)taken (new run, or it has just stopped);
        False: record updated, the snapshot stays."""
        old = self.df.loc[run].to_dict() if run in self.df.index else None
        if old is not None and old.get("runstatus") == 0 and row["runstatus"] == 0 and run in self.motors:
            return None
        just_stopped = old is not None and old.get("runstatus") != 0 and row["runstatus"] == 0
        self.df.loc[run] = [row.get(key, math.nan if key in NUMERIC else "") for key in COLUMNS[1:]]
        return run not in self.motors or just_stopped

    def _finish(self, run: int, row: Dict[str, Any], changed: List[int]) -> None:
        row["moving"] = ";".join(names.display_name(self.cfg, m)
                                 for m in str(self.motors[run].get(scan.MOVING, "")).split(";") if m)
        self.df.at[run, "moving"] = row["moving"]
        changed.append(run)
        _print_row(row)
        if len(changed) % self.CHECKPOINT == 0:      # checkpoint: a restart continues from here
            self.save_now()

    def save_now(self) -> None:
        scan.save_motors(self.motors, self.motors_path)
        save(self.df, self.path)

    def _pass(self, candidates: List[int], changed: List[int]) -> None:
        if self.workers > 1 and len(candidates) > 1:
            self._pass_parallel(candidates, changed)
            return
        for run in candidates:
            if self.heartbeat is not None:
                self.heartbeat()
            row = record(self.bl, run)
            if row is None:
                continue
            need = self._apply(run, row)
            if need is None:
                continue
            # snapshot: once when the run appears, once more when it has stopped (not on
            # every pass while it is running)
            if need:
                self.motors[run] = self._snapshot(run)
            self._finish(run, row, changed)

    def _wait(self, job: Any) -> Any:
        """Result of a pool job, touching the lock (heartbeat) while waiting."""
        while not job.ready():
            if self.heartbeat is not None:
                self.heartbeat()
            job.wait(5.0)
        return job.get()

    def _pass_parallel(self, candidates: List[int], changed: List[int]) -> None:
        """_pass with self.workers processes (see the module text)."""
        import multiprocessing

        ctx = multiprocessing.get_context("spawn")
        n = self.workers
        with ctx.Pool(n, initializer=_worker_init, initargs=(self.cfg.path,)) as pool:
            t0 = time.time()
            print("reading the records of {} runs with {} workers ...".format(len(candidates), n), flush=True)
            chunk = max(1, min(50, len(candidates) // (4 * n) or 1))
            rows = self._wait(pool.map_async(_record_job, [(self.bl, r) for r in candidates], chunksize=chunk))
            records = {r: row for r, row in zip(candidates, rows) if row is not None}
            todo = []  # type: List[int]
            for r in sorted(records):
                need = self._apply(r, records[r])
                if need:
                    todo.append(r)
                elif need is False:                     # record updated, snapshot stays
                    self._finish(r, records[r], changed)
            print("records: {:.0f} s; motor snapshots of {} runs ...".format(time.time() - t0, len(todo)), flush=True)
            # whole scans per job (their runs in order); plain runs alone
            group_of = {}  # type: Dict[int, List[int]]
            for g in scan.group_runs(self.df):
                for r in g:
                    group_of[r] = g
            jobs = []  # type: List[List[int]]
            seen = set()  # type: Set[int]
            for r in todo:
                if r in seen:
                    continue
                g = group_of.get(r, [r])
                runs = [x for x in g if x in todo]
                seen.update(runs)
                jobs.append(runs)
            # batches of about CHECKPOINT runs, saved after each
            batch = []  # type: List[List[int]]
            batches = []  # type: List[List[List[int]]]
            for job in jobs:
                batch.append(job)
                if sum(len(j) for j in batch) >= self.CHECKPOINT:
                    batches.append(batch)
                    batch = []
            if batch:
                batches.append(batch)
            for b in batches:
                payloads = []  # type: List[Any]
                for runs in b:
                    g = group_of.get(runs[0], runs)
                    sub = self.df.loc[[x for x in g if x in self.df.index]]
                    payloads.append((sub, {x: self.motors[x] for x in g if x in self.motors}, runs))
                results = self._wait(pool.map_async(_snapshot_job, payloads, chunksize=1))
                for snaps in results:
                    for r in sorted(snaps):
                        self.motors[r] = snaps[r]
                        self._finish(r, records[r], changed)
                self.save_now()
            print("motor snapshots done ({:.0f} s in total)".format(time.time() - t0), flush=True)

    def run(self, follow: bool = False, poll: float = 5.0, rebuild: bool = False,
            first_run: Optional[int] = None, last_run: Optional[int] = None, workers: int = 1) -> None:
        """Update the log. follow=True: keep watching for new runs (Ctrl-C stops).
        rebuild=True: start from first_run with an empty log. workers > 1: read with that many
        processes (offline; capped at the job's cores)."""
        from silas.core import runner

        last_run = last_run or self.cfg.get("last_run", None)

        self.workers = runner.cap_workers(workers) if workers > 1 else 1
        lock = _Lock(self.path)
        lock.acquire()
        self.heartbeat = lock.touch
        try:
            if rebuild:
                self.df = empty()
                self.motors = {}
                self.checked_up_to = None
                save(self.df, self.path)
            print("run log: {}  (beamline {}, {})".format(
                self.path, self.bl, "following new runs, Ctrl-C to stop" if follow else "one pass"))
            print("scans:   {}".format(self.scans_path))
            self.update_once(first_run, last_run)
            lock.touch()
            while follow:
                time.sleep(poll)
                self.update_once(first_run, last_run)
                lock.touch()
        except KeyboardInterrupt:
            print("\nstopped; log is up to date in {}".format(self.path))
        finally:
            lock.release()


# ---------------------------------------------------------------- parallel workers (offline)
_WORKER = {}  # type: Dict[str, Any]


def _worker_init(config_path: str) -> None:
    """Pool initializer: Ctrl-C is handled by the main process; one RunLogger per worker for the
    motor snapshots (its log and motors are replaced per job)."""
    from silas.core import runner

    runner.worker_ignores_ctrl_c()
    _WORKER["logger"] = RunLogger(Config(config_path))


def _record_job(payload: Tuple[int, int]) -> Optional[Dict[str, Any]]:
    bl, run = payload
    return record(bl, run)


def _snapshot_job(payload: Tuple[Any, Dict[int, Dict[str, Any]], List[int]]) -> Dict[int, Dict[str, Any]]:
    """Motor snapshots of runs of one scan (or one plain run), in run order. payload: the scan's
    rows of the run log, the motor snapshots it already has, the runs to take."""
    sub, motors, runs = payload
    lg = _WORKER["logger"]
    lg.df = sub
    lg.motors = dict(motors)
    out = {}  # type: Dict[int, Dict[str, Any]]
    for r in runs:
        lg.motors[r] = lg._snapshot(r)
        out[r] = lg.motors[r]
    return out
