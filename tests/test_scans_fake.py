#!/usr/bin/env python3
"""Logic test of the scan table against a FAKE dbpy (no SACLA library is used).

    python tests/test_scans_fake.py

Fake beamline 3 (one run per scan step, as SACLA does it):
  100        plain run
  101-106    scan A, 5 steps of delay_m (st_2_motor_1): 1100 + 34 per step,
             step 3 repeated (runs 103, 104), step 5 missing (incomplete)
  107        plain run (dark)
  108-       scan B, 3 steps of a motor that is NOT configured (st_3_motor_8),
             followed online: first only step 1 (running), then steps 2 and 3
Always present: fluence_2 = -14060 (a condition), a noisy "motor" (st_3_motor_9)
that changes within every run (never a scan motor), equipment of another beamline.
Everything is written to a temporary folder; your config and logs are untouched.
"""
import math
import os
import sys
import tempfile
import types
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DELAY = "xfel_bl_3_st_2_motor_1/position"
ND = "xfel_bl_3_st_2_motor_2/position"
OTHER = "xfel_bl_3_st_3_motor_8/position"
NOISY = "xfel_bl_3_st_3_motor_9/position"
BL2 = "xfel_bl_2_st_1_motor_1/position"

# run -> (status, step, total, {motor: value})
RUNS = {}  # type: Dict[int, Any]
RUNS[100] = [0, None, None, {DELAY: 1000.0}]
for run, step in zip(range(101, 106), (1, 2, 3, 3, 4)):
    RUNS[run] = [0, step, 5, {DELAY: 1100.0 + 34 * (step - 1)}]
RUNS[106] = [0, None, None, {DELAY: 1100.0}]          # scan stopped; back to start (plain run)
RUNS[107] = [0, None, None, {DELAY: 1100.0}]
RUNS[108] = [2, 1, 3, {DELAY: 1100.0, OTHER: 5.0}]    # scan B step 1, running


class APIError(Exception):
    pass


def _check(run):
    if run not in RUNS:
        raise APIError(-107, "DA_ERR_BADRUN")


def _tags(run):
    return tuple(range(run * 10000, run * 10000 + 2000, 2))


def _run_of_tag(tag):
    return tag // 10000


def _supp(bl, run):
    _check(run)
    st, step, total, _ = RUNS[run]
    if step is None:
        return []
    t = _tags(run)
    return [{"stepnumber": step, "total_stepnumber": total, "start_tagnumber": t[0],
             "end_tagnumber": t[-1], "scanmode": "sync", "comment": ""}]


# run -> equipment read (to check the reduced search)
READS = {}  # type: Dict[int, set]


def _sync(equip, hi, tags):
    READS.setdefault(_run_of_tag(tags[0]), set()).add(equip)
    vals = []
    for i, tag in enumerate(tags):
        run = _run_of_tag(tag)
        motors = dict(RUNS[run][3])
        motors.setdefault(ND, -14060.0)
        motors.setdefault(OTHER, 0.0)
        motors[NOISY] = 3.0 + 0.1 * i
        if equip not in motors:
            raise APIError(-200, "DA_ERR_NODATA")
        vals.append(motors[equip])
    return vals


fake = types.ModuleType("dbpy")  # type: Any
fake.APIError = APIError
fake.read_runnumber_newest = lambda bl: max(RUNS)
fake.read_runstatus = lambda bl, run: (_check(run), RUNS[run][0])[1]
fake.read_runinfo = lambda bl, run: (_check(run), {
    "starttime": 1.79e9 + run * 60.0, "stoptime": 1.79e9 + run * 60.0 + 30.0,
    "total_tagnumber": 1000, "start_tagnumber": _tags(run)[0], "end_tagnumber": _tags(run)[-1],
    "hightagnumber": 202601, "comment": "time scan\nND -14060" if RUNS[run][1] else "plain run",
    "fy_id": "2026B0000", "detectors": "MPCCD-8N0-3-002-1", "runtype": "normal",
    "stationnumber": 3, "runstatus": RUNS[run][0]})[1]
fake.read_detidlist = lambda bl, run: ("MPCCD-8N0-3-002-1",)
fake.read_run_supplementinfo = _supp
fake.read_equiplist = lambda: (DELAY, ND, OTHER, NOISY, BL2, "xfel_bl_3_st_2_pd_user_6_fitting_peak/voltage")
fake.read_taglist_byrun = lambda bl, run: _tags(run)
fake.read_hightagnumber = lambda bl, run: 202601
fake.read_syncdatalist_float = _sync
sys.modules["dbpy"] = fake

# ---------------------------------------------------------------- test
tmp = tempfile.mkdtemp(prefix="silas_test_")
os.environ["SILAS_PROFILE"] = "local"
os.environ["SILAS_CONFIG"] = os.path.join(tmp, "test.json")

from silas.core import runlog, scan  # noqa: E402
from silas.core.config import Config  # noqa: E402

from silas.core import standard  # noqa: E402

cfg = Config()                     # a new config
standard.add_standard_detectors(cfg, beamline=3, quiet=True)   # new configs start empty
from silas.core import names  # noqa: E402

names.add_point_detector(cfg, ND, alias="fluence_2", unit="pulse")   # not standard any more
cfg.set("paths.output", tmp)
cfg.set("first_run", 100)
log = runlog.RunLogger(cfg)

print("--- pass 1: scan A done, scan B at step 1 (running)")
log.update_once()
scans = scan.load_scans(scan.scans_path(cfg))
print(scans.T.to_string())
a = scans.loc[101]
assert list(scans.index) == [101, 108], list(scans.index)
assert a["runs"] == "101-105" and a["n_runs"] == 5 and a["steps_done"] == 4, a
assert a["scan_motor"] == DELAY and a["scan_motor_alias"] == "delay_m" and a["unit"] == "pulse", a
assert (a["start_value"], a["end_value"], a["step_size"]) == (1100, 1202, 34), a
assert a["repeated_steps"] == "3 (103, 104)" and a["missing_steps"] == "5", a
assert a["status"] == "incomplete", a["status"]
assert "fluence_2=-14060" in a["conditions"] and "delay_m" not in a["conditions"], a["conditions"]
assert a["other_scanned"] == "", a["other_scanned"]
b = scans.loc[108]
assert b["status"] == "running" and b["scan_motor"] == "" and b["n_runs"] == 1, b

df = runlog.load(log.path)
assert math.isnan(df.loc[100, "scan_id"]) and df.loc[104, "scan_id"] == 101 and df.loc[104, "step"] == 3
assert df.loc[105, "scan_value"] == 1202 and df.loc[105, "scan_motor"] == DELAY
assert "st_3_motor_9" in df.loc[101, "moving"], df.loc[101, "moving"]   # noisy one moves within runs
print("motors read:", {r: len(v) for r, v in sorted(READS.items())})
assert BL2 not in READS[101] and OTHER in READS[101] and NOISY in READS[101]   # step 1: all BL3 motors
assert OTHER in READS[102]                          # step 2: those with data in step 1
assert OTHER not in READS[103] and NOISY not in READS[103] and DELAY in READS[103]  # known: no search
assert OTHER not in READS[100] and OTHER not in READS[107]                    # plain runs: configured
motors = scan.load_motors(scan.motors_path(cfg))
assert motors[103][DELAY] == 1168 and BL2 not in motors[103], motors[103]

print("\n--- pass 2: scan B continues (108 stops, steps 2 and 3 arrive)")
RUNS[108][0] = 0
RUNS[109] = [0, 2, 3, {DELAY: 1100.0, OTHER: 5.5}]
RUNS[110] = [2, 3, 3, {DELAY: 1100.0, OTHER: 6.0}]
log.update_once()
b = scan.load_scans(scan.scans_path(cfg)).loc[108]
assert b["scan_motor"] == OTHER and b["scan_motor_alias"] == OTHER and b["step_size"] == 0.5, b
assert OTHER in READS[110] and NOISY not in READS[110], READS[110]   # unconfigured scan motor is followed
assert b["status"] == "running" and b["missing_steps"] == "", b
df = runlog.load(log.path)
assert df.loc[108, "scan_value"] == 5.0, df.loc[108]           # step 1 filled in afterwards

print("\n--- pass 3: 110 stops -> scan B complete; a new logger continues from the files")
RUNS[110][0] = 0
runlog.RunLogger(cfg).update_once()
b = scan.load_scans(scan.scans_path(cfg)).loc[108]
assert b["status"] == "complete" and b["stop_jst"] != "", b

print("\n--- an old run log without scan columns is upgraded")
os.remove(scan.motors_path(cfg))
changed = runlog.RunLogger(cfg).update_once()
assert changed == list(range(100, 111)), changed

print("\n--- rebuild gives the same scan table")
before = scan.load_scans(scan.scans_path(cfg))
runlog.RunLogger(cfg).run(rebuild=True)
after = scan.load_scans(scan.scans_path(cfg))
assert before.drop(columns=["stop_jst"]).equals(after.drop(columns=["stop_jst"])), (before, after)

print("\n--- session commands")
from silas.cli import commands  # noqa: E402

c = commands.build(cfg)
assert c["scan_range"]() == (108, 110) and c["scan_range"](0) == (101, 105) and c["scan_range"](103) == (101, 105)
assert c["scan_motor"](-2) == ("delay_m", DELAY) and c["scan_motor"]() == (OTHER, OTHER)
assert c["scan_run_list"](-2) == [101, 102, 103, 104, 105]
assert c["scan_info"](101)["missing_steps"] == "5"
assert len(c["scan_runs"](0)) == 5 and list(c["show_scans"]().index) == [101, 108]
for bad in (5, 100):
    try:
        c["scan_info"](bad)
        raise AssertionError("scan_info({}) did not fail".format(bad))
    except LookupError as err:   # IndexError is a LookupError too
        print("  refused as expected:", err)

print("\nOK  (files in {})".format(tmp))
print(after[["runs", "steps_done", "total_steps", "status", "scan_motor_alias", "start_value",
             "end_value", "step_size", "repeated_steps", "missing_steps"]].to_string())
