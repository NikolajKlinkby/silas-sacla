#!/usr/bin/env python3
"""Logic test of the run logger against a FAKE dbpy (no SACLA library is used).

    python tests/test_runlog_fake.py

Fake beamline: runs 100..110, run 105 belongs to another beamline (does not
exist here), run 110 is still running. Then run 110 stops and 111 appears. Then the batch-job
behaviour: a parallel pass in chunks saves the log as it goes, follow stops when last_run is
done (status file), and SIGTERM stops a pass cleanly.
Everything is written to a temporary folder; your config and logs are untouched.
"""
import os
import sys
import tempfile
import types
from typing import Any, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------- fake dbpy
STATUS = {r: 0 for r in range(100, 111) if r != 105}
STATUS[110] = 2


class APIError(Exception):
    pass


def _check(run):
    if run not in STATUS:
        raise APIError(-107, "DA_ERR_BADRUN")


fake = types.ModuleType("dbpy")  # type: Any
fake.APIError = APIError
fake.read_runnumber_newest = lambda bl: max(STATUS)
fake.read_runstatus = lambda bl, run: (_check(run), STATUS[run])[1]
fake.read_runinfo = lambda bl, run: (_check(run), {
    "starttime": 1.79e9 + run * 60.0, "stoptime": 1.79e9 + run * 60.0 + 30.0,
    "total_tagnumber": 900, "start_tagnumber": run * 1000, "end_tagnumber": run * 1000 + 1798,
    "hightagnumber": 202601, "comment": "fake run {}".format(run), "fy_id": "2026B0000",
    "detectors": "MPCCD-2N0-M02-001", "runtype": "normal", "stationnumber": 3,
    "runstatus": STATUS[run]})[1]
fake.read_detidlist = lambda bl, run: ("MPCCD-2N0-M02-001", "OPAL-123")


def _no_data(*args):
    raise APIError(-200, "DA_ERR_NODATA")


fake.read_run_supplementinfo = _no_data          # no scans in this test (see test_scans_fake.py)
fake.read_equiplist = lambda: ()
fake.read_taglist_byrun = lambda bl, run: tuple(range(run * 1000, run * 1000 + 1799, 2))
fake.read_hightagnumber = lambda bl, run: 202601
fake.read_syncdatalist_float = _no_data
sys.modules["dbpy"] = fake

# ---------------------------------------------------------------- test
tmp = tempfile.mkdtemp(prefix="silas_test_")
os.environ["SILAS_PROFILE"] = "local"
os.environ["SILAS_CONFIG"] = os.path.join(tmp, "test.json")

from silas.core import runlog  # noqa: E402
from silas.core.config import Config  # noqa: E402

cfg = Config()
cfg.set("paths.output", tmp)
cfg.set("first_run", 100)
log = runlog.RunLogger(cfg)

print("--- pass 1")
changed = log.update_once()
assert changed == [100, 101, 102, 103, 104, 106, 107, 108, 109, 110], changed
df = runlog.load(log.path)
assert 105 not in df.index and df.loc[110, "status"] == "running", df

print("--- pass 2 (110 stops, 111 appears)")
STATUS[110] = 0
STATUS[111] = 2
changed = log.update_once()
assert changed == [110, 111], changed
df = runlog.load(log.path)
assert df.loc[110, "status"] == "stopped" and df.loc[110, "duration_s"] == 30.0, df.loc[110]
assert df.loc[110, "runstatus"] == 0 and df.loc[111, "runstatus"] == 2

print("--- pass 3 (only the running run 111 is re-checked)")
assert log.update_once() == [111]  # still running -> re-checked and rewritten

print("--- new logger continues after the last logged run")
log2 = runlog.RunLogger(cfg)
STATUS[111] = 0
assert log2.update_once() == [111]

print("--- lock: a second logger on the same log is refused")
lock = runlog._Lock(log.path)
lock.acquire()
try:
    runlog.RunLogger(cfg).run()
    raise AssertionError("second logger was not refused")
except RuntimeError as err:
    print("  refused as expected:", err)
finally:
    lock.release()

print("--- rebuild")
runlog.RunLogger(cfg).run(rebuild=True)
assert list(runlog.load(log.path).index) == [100, 101, 102, 103, 104, 106, 107, 108, 109, 110, 111]

# ---------------------------------------------------------------- live batch-job behaviour
print("--- parallel pass in chunks: the log grows during the pass, scans.csv with it")
import multiprocessing  # noqa: E402
import signal  # noqa: E402


class _Job(object):
    def __init__(self, value: Any) -> None:
        self.value = value

    def ready(self) -> bool:
        return True

    def wait(self, t: float = 0.0) -> None:
        pass

    def get(self) -> Any:
        return self.value


class _InlinePool(object):
    """multiprocessing's Pool, in this process (the fake dbpy exists only here)."""

    def __init__(self, n: int, initializer: Any = None, initargs: Any = ()) -> None:
        if initializer is not None:
            initializer(*initargs)

    def map_async(self, func: Any, items: Any, chunksize: int = 1) -> _Job:
        return _Job([func(x) for x in items])

    def __enter__(self) -> "_InlinePool":
        return self

    def __exit__(self, *a: Any) -> None:
        pass


class _Ctx(object):
    Pool = _InlinePool


real_get_context = multiprocessing.get_context
multiprocessing.get_context = lambda method=None: _Ctx()  # type: ignore
sizes = []  # type: List[int]
real_save = runlog.save


def counting_save(df: Any, path: str) -> None:
    if path.endswith("runlog.csv"):
        sizes.append(len(df))
    real_save(df, path)


runlog.save = counting_save
for r in range(112, 140):
    STATUS[r] = 0
lg = runlog.RunLogger(cfg)
lg.PASS_CHUNK, lg.CHECKPOINT, lg.workers = 7, 3, 2
changed = lg.update_once()
assert changed == list(range(112, 140)), changed
assert len(set(sizes)) == 4 and sizes == sorted(sizes), sizes      # every chunk of 7 saved, growing
assert os.path.exists(os.path.join(tmp, "scans.csv"))
print("  ok: {} runs in chunks of 7, the log saved {} times while growing ({} .. {} rows)".format(
    len(changed), len(sizes), sizes[0], sizes[-1]))
runlog.save = real_save

print("--- follow until last_run is stopped; status file; run_logger_status text")
STATUS[140] = 2                                       # still running at the first pass
sleeps = []  # type: List[float]
real_sleep = runlog.time.sleep


def fake_sleep(t: float) -> None:                     # the run stops while the logger waits
    sleeps.append(t)
    STATUS[140] = 0


runlog.time.sleep = fake_sleep
runlog.RunLogger(cfg).run(follow=True, poll=1.0, last_run=140)
runlog.time.sleep = real_sleep
st = runlog.read_status(log.path)
assert len(sleeps) == 1 and st["phase"] == "finished" and st["last_logged_run"] == 140, (sleeps, st)
assert runlog.load(log.path).loc[140, "status"] == "stopped"
ok, text = runlog.logger_status(cfg, detail=True)
assert not ok and "ended" in text and "finished" in text and "140" in text, text
print("  ok: stopped after run 140 finished; status: " + text.replace("\n", " | "))

print("--- SIGTERM (qdel / walltime) in the middle of a pass: saved, lock removed, a new logger continues")
for r in range(141, 160):
    STATUS[r] = 0
real_runinfo = fake.read_runinfo


def runinfo_term(bl: int, run: int) -> Any:
    if run == 150:
        os.kill(os.getpid(), signal.SIGTERM)
    return real_runinfo(bl, run)


fake.read_runinfo = runinfo_term
lg = runlog.RunLogger(cfg)
lg.CHECKPOINT = 4
lg.run()
fake.read_runinfo = real_runinfo
df = runlog.load(log.path)
assert 148 in df.index and 159 not in df.index, list(df.index)[-5:]
assert not os.path.exists(log.path + ".lock")
assert runlog.read_status(log.path)["phase"].startswith("stopped")
assert runlog.RunLogger(cfg).update_once()[-1] == 159
print("  ok: stopped at run {}, the rest done by the next logger".format(int(df.index.max())))
multiprocessing.get_context = real_get_context

print("\nOK  (log written to {})".format(log.path))
print(runlog.load(log.path)[["status", "start_jst", "duration_s", "comment", "detectors"]].to_string())
