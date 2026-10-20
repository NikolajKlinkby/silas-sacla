"""What was scanned: steps and scanned motors, from dbpy (online and offline).

SACLA's scan tool makes ONE RUN PER STEP (checked on stored time scans). dbpy.read_run_supplementinfo(bl, run)
returns one entry per run with stepnumber (1..total_stepnumber), the run's tag
range, scanmode (e.g. "sync") and comment. The scanned motor itself is not named;
it is the motor whose value changes from run to run (and stays constant within a run).

So the run logger takes a "motor snapshot" of every run (motor values at a few tags) and
finds the scanned motor by comparing the steps of a scan with each other. Only point
detectors with "motor" and "position" in the name are taken to be scannable. Reading all of
them is slow, so the full search is only done until the scanned motor is known:

  * step 1 of a scan:   all BL motors ("full" snapshot)
  * step 2:             the motors that had data in step 1
  * later steps:        the configured motors + the scanned motor(s) only
  * plain runs:         the configured motors only (for the "conditions")
The scan table (<output>/scans.csv) is rebuilt from the run log and the snapshots:

  * a run with step information belongs to a scan; a run without it is a plain run
  * a new scan starts at step 1 (unless the open scan has only had step 1 so far: a
    repeated step 1) or when the total number of steps changes; plain runs in between
    do not end a scan
  * the scanned motor is a motor that is constant within every run of the scan and has
    different values in different steps. Configured motors are preferred; others that
    also change are listed in "other_scanned". It is known from the second step on.
  * repeated steps are kept (both runs are in the scan); bad data is sorted out later

    scan_overview(1234501, 1234551)     # session command: one row per run, straight from dbpy
    show_scans()                        # the scan table written by the run logger
"""
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from silas.core import db, names
from silas.core.config import Config


def step_info(bl: int, run: int) -> Dict[str, Any]:
    """SACLA's step information of a run: stepnumber, total_stepnumber, scanmode, comment
    (empty dict if the run is not part of a scan)."""
    import dbpy

    try:
        sup = dbpy.read_run_supplementinfo(bl, run)
    except dbpy.APIError:
        return {}
    if not sup:
        return {}
    first = dict(sup[0])
    first["n_entries"] = len(sup)  # >1 would mean several steps inside one run
    return first


def motor_names(cfg: Config) -> List[str]:
    """Point detectors that are motors: true names containing 'motor' (aliases work as usual)."""
    return [n for n in names.point_detector_ids(cfg) if "motor" in n]


def run_values(bl: int, run: int, equip_ids: Sequence[str], n_sample: int = 50) -> Dict[str, Dict[str, float]]:
    """Median, min and max of each point detector over ``n_sample`` tags of the run."""
    tags = db.tags(bl, run)
    if not tags:
        return {}
    step = max(1, len(tags) // n_sample)
    sample = tags[::step]
    hi = db.high_tag(bl, run)
    out = {}  # type: Dict[str, Dict[str, float]]
    for e in equip_ids:
        vals = [v for v in db.read_point(e, hi, sample, errors="nan") if not math.isnan(v)]
        if not vals:
            out[e] = {"median": math.nan, "min": math.nan, "max": math.nan}
            continue
        vals.sort()
        out[e] = {"median": vals[len(vals) // 2], "min": vals[0], "max": vals[-1]}
    return out


def overview(cfg: Config, first_run: int, last_run: Optional[int] = None,
             equip_ids: Optional[Sequence[str]] = None, n_sample: int = 50) -> Any:
    """pandas DataFrame, one row per run of [first_run, last_run]: step, total_steps,
    scanmode, comment, and per motor its median value (column = display name) and
    whether it moved within the run ("<name> moving"). df.attrs["scanned"] lists the
    motors whose value changes between runs."""
    import pandas as pd

    bl = cfg.beamline
    last_run = first_run if last_run is None else last_run
    equip = list(equip_ids) if equip_ids is not None else motor_names(cfg)
    rows = []
    for run in range(first_run, last_run + 1):
        try:
            if db.run_status(bl, run) < 0:
                continue
            info = db.run_info(bl, run)
        except db.api_error():
            continue
        st = step_info(bl, run)
        row = {"run": run, "step": st.get("stepnumber"), "total_steps": st.get("total_stepnumber"),
               "scanmode": st.get("scanmode", ""), "step_comment": st.get("comment", ""),
               "comment": str(info.get("comment", "")).replace("\n", " | ")}  # type: Dict[str, Any]
        for e, v in run_values(bl, run, equip, n_sample).items():
            label = names.display_name(cfg, e)
            row[label] = v["median"]
            row[label + " moving"] = bool(v["max"] != v["min"])
        rows.append(row)
        print("run {}  step {}/{}  {}".format(run, row["step"], row["total_steps"], row["comment"][:70]))
    df = pd.DataFrame(rows).set_index("run") if rows else pd.DataFrame()
    scanned = []
    for e in equip:
        label = names.display_name(cfg, e)
        if label in df.columns and df[label].nunique(dropna=True) > 1:
            scanned.append(label)
    df.attrs["scanned"] = scanned
    print("\nmotors that change between runs (scanned): {}".format(", ".join(scanned) or "none"))
    moving = [c[:-len(" moving")] for c in df.columns if c.endswith(" moving") and df[c].any()]
    print("motors that move within a run: {}".format(", ".join(moving) or "none"))
    return df


# ================================================================ motor snapshots
MOVING = "_moving"
"""Key in a snapshot: true names of the motors that moved within the run, ';'-separated."""
FULL = "_full"
"""Key in a snapshot: 1 if all BL motors were searched (step 1 of a scan), else 0."""


def motor_keys(snap: Dict[str, Any]) -> List[str]:
    """The motors in a snapshot (without the MOVING / FULL entries)."""
    return [k for k in snap if not k.startswith("_")]

SCAN_COLUMNS = [
    "scan_id", "first_run", "last_run", "n_runs", "runs", "total_steps", "steps_done", "status",
    "scan_motor", "scan_motor_alias", "unit", "start_value", "end_value", "step_size",
    "repeated_steps", "missing_steps", "other_scanned", "conditions", "scanmode",
    "start_jst", "stop_jst", "n_tags", "comment",
]
SCAN_NUMERIC = ("first_run", "last_run", "n_runs", "total_steps", "steps_done", "start_value",
                "end_value", "step_size", "n_tags")


def same(a: float, b: float) -> bool:
    """Equal within rounding (motor positions are compared with this)."""
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


def candidate_motors(cfg: Config, equipment: Optional[Sequence[str]] = None) -> List[str]:
    """Motors checked for scans: the configured motors first, then (config
    "scan.search" = "all", the default) every "xfel_bl_<bl>_*motor*position*" equipment."""
    out = motor_names(cfg)
    if cfg.get("scan.search", "all") == "all" and equipment:
        prefix = "xfel_bl_{}_".format(cfg.beamline)
        out += sorted(e for e in equipment
                      if e.startswith(prefix) and "motor" in e and "position" in e and e not in out)
    return out


def _read_quiet(equip_id: str, taghi: int, tag_list: Sequence[int]) -> Optional[List[float]]:
    """Values, or None if the equipment has no data for these tags."""
    import dbpy

    try:
        return [float(v) for v in dbpy.read_syncdatalist_float(equip_id, taghi, tuple(tag_list))]
    except (dbpy.APIError, TypeError, ValueError):
        return None


def snapshot(bl: int, run: int, equip_ids: Sequence[str], n_sample: int = 5) -> Dict[str, Any]:
    """{true name: median value} of the motors at ``n_sample`` tags spread over the run
    (first and last tag included), plus MOVING: the motors that changed within the run.
    Equipment without data in the run is left out."""
    tag_list = db.tags(bl, run)
    if not tag_list:
        return {}
    n = max(1, min(n_sample, len(tag_list)))
    idx = sorted(set(int(round(i * (len(tag_list) - 1) / max(1, n - 1))) for i in range(n)))
    sample = [tag_list[i] for i in idx]
    hi = db.high_tag(bl, run)
    out = {}  # type: Dict[str, Any]
    moving = []
    for e in equip_ids:
        vals = _read_quiet(e, hi, sample)
        if vals is None:
            continue
        vals = sorted(v for v in vals if not math.isnan(v))
        if not vals:
            continue
        out[e] = vals[len(vals) // 2]
        if not same(vals[0], vals[-1]):
            moving.append(e)
    out[MOVING] = ";".join(moving)
    return out


def motors_path(cfg: Config) -> str:
    return os.path.join(cfg.output_dir(), "motors.csv")


def scans_path(cfg: Config) -> str:
    return os.path.join(cfg.output_dir(), "scans.csv")


def load_motors(path: str) -> Dict[int, Dict[str, Any]]:
    """Motor snapshots per run from <output>/motors.csv."""
    import pandas as pd

    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, index_col="run", dtype=str, keep_default_na=False)
    out = {}  # type: Dict[int, Dict[str, Any]]
    for run, row in df.iterrows():
        snap = {}  # type: Dict[str, Any]
        for k, v in row.items():
            if k == MOVING:
                snap[k] = v
            elif k == FULL:
                snap[k] = 1 if v not in ("", "0", "0.0") else 0
            elif v != "":
                try:
                    snap[k] = float(v)
                except ValueError:
                    pass
        out[int(run)] = snap
    return out


def save_motors(motors: Dict[int, Dict[str, Any]], path: str) -> None:
    import pandas as pd

    from silas.core import runlog

    cols = sorted({k for snap in motors.values() for k in motor_keys(snap)})
    df = pd.DataFrame.from_dict(motors, orient="index").reindex(columns=[MOVING, FULL] + cols)
    df.index.name = "run"
    runlog.save(df, path)


def load_scans(path: str) -> Any:
    """The scan table as a pandas DataFrame indexed by scan_id (empty if no file yet)."""
    import pandas as pd

    if not os.path.exists(path):
        return pd.DataFrame(columns=SCAN_COLUMNS).set_index("scan_id")
    df = pd.read_csv(path, index_col="scan_id", dtype=str, keep_default_na=False)
    df.index = df.index.astype(int)
    df = df.reindex(columns=SCAN_COLUMNS[1:], fill_value="")
    for col in SCAN_NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ================================================================ scan table
def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def ranges(values: Sequence[int]) -> str:
    """[1, 2, 3, 5, 7, 8] -> '1-3, 5, 7-8'."""
    vals = sorted(set(int(v) for v in values))
    parts = []
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[j + 1] == vals[j] + 1:
            j += 1
        parts.append(str(vals[i]) if i == j else "{}-{}".format(vals[i], vals[j]))
        i = j + 1
    return ", ".join(parts)


def group_runs(log: Any) -> List[List[int]]:
    """Runs of the run log grouped into scans (lists of runs, in run order)."""
    scans = []  # type: List[List[int]]
    open_scan = None  # type: Optional[List[int]]
    open_total = None  # type: Optional[float]
    for run in sorted(int(r) for r in log.index):
        step = _num(log.at[run, "step"])
        if step is None:
            continue  # plain run
        total = _num(log.at[run, "total_steps"])
        only_step1 = open_scan is not None and all(_num(log.at[r, "step"]) == 1 for r in open_scan)
        if open_scan is None or total != open_total or (step == 1 and not only_step1):
            open_scan = []
            open_total = total
            scans.append(open_scan)
        open_scan.append(run)
    return scans


def _fmt(v: float) -> str:
    return "{:g}".format(v)


def scan_row(cfg: Config, log: Any, runs: List[int],
             motors: Dict[int, Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """(row of the scan table, true names of the scanned motors, main one first; [] = not known)."""
    steps = {r: int(_num(log.at[r, "step"]) or 0) for r in runs}
    total = _num(log.at[runs[0], "total_steps"])
    configured = motor_names(cfg)
    # first run of every step: one value per step
    first_of_step = {}  # type: Dict[int, int]
    for r in runs:
        first_of_step.setdefault(steps[r], r)
    step_runs = [first_of_step[s] for s in sorted(first_of_step)]

    snaps = [motors.get(r, {}) for r in runs]
    moving = set()  # type: set
    for sn in snaps:
        moving.update(m for m in str(sn.get(MOVING, "")).split(";") if m)
    common = set.intersection(*[set(motor_keys(sn)) for sn in snaps]) if snaps else set()
    scanned = []
    if len(step_runs) > 1:
        for m in common - moving:
            vals = [motors[r][m] for r in step_runs]
            if any(not same(v, vals[0]) for v in vals[1:]):
                scanned.append(m)
    scanned.sort(key=lambda m: (configured.index(m) if m in configured else len(configured), m))
    motor = scanned[0] if scanned else ""

    row = {"scan_id": runs[0], "first_run": runs[0], "last_run": runs[-1], "n_runs": len(runs),
           "runs": ranges(runs), "total_steps": total, "steps_done": len(first_of_step)}  # type: Dict[str, Any]
    stopped = all(_num(log.at[r, "runstatus"]) == 0 for r in runs)
    expected = set(range(1, int(total) + 1)) if total else set()
    if not stopped:
        expected = set(range(1, max(steps.values()) + 1))
    missing = sorted(expected - set(first_of_step))
    row["status"] = "running" if not stopped else ("complete" if not missing else "incomplete")
    row["missing_steps"] = ranges(missing)
    rep = [s for s in sorted(first_of_step) if sum(1 for r in runs if steps[r] == s) > 1]
    row["repeated_steps"] = "; ".join("{} ({})".format(s, ", ".join(str(r) for r in runs if steps[r] == s))
                                      for s in rep)
    row["scan_motor"] = motor
    row["scan_motor_alias"] = names.display_name(cfg, motor) if motor else ""
    row["unit"] = names.entry(cfg, motor).get("unit", "") if motor else ""
    row["other_scanned"] = ";".join(names.display_name(cfg, m) for m in scanned[1:])
    if motor:
        vals = [motors[r][motor] for r in step_runs]
        row["start_value"], row["end_value"] = vals[0], vals[-1]
        diffs = sorted((vals[i + 1] - vals[i]) / (sorted(first_of_step)[i + 1] - sorted(first_of_step)[i])
                       for i in range(len(vals) - 1))
        row["step_size"] = diffs[len(diffs) // 2]
    else:
        row["start_value"] = row["end_value"] = row["step_size"] = math.nan
    conds = []
    for m in configured:
        if m in scanned or m not in common or m in moving:
            continue
        vals = [motors[r][m] for r in runs]
        if all(same(v, vals[0]) for v in vals):
            conds.append("{}={}".format(names.display_name(cfg, m), _fmt(vals[0])))
    row["conditions"] = "; ".join(conds)
    row["scanmode"] = log.at[runs[0], "scanmode"]
    row["start_jst"] = log.at[runs[0], "start_jst"]
    row["stop_jst"] = log.at[runs[-1], "stop_jst"] if stopped else ""
    row["n_tags"] = sum(_num(log.at[r, "total_tagnumber"]) or 0 for r in runs)
    row["comment"] = log.at[runs[0], "comment"]
    return row, scanned


def build_scans(cfg: Config, log: Any, motors: Dict[int, Dict[str, Any]]) -> Tuple[Any, Dict[int, Dict[str, Any]]]:
    """(scan table, {run: {scan_id, scan_motor, scan_value}}) from the run log and the
    motor snapshots."""
    import pandas as pd

    rows = []
    per_run = {}  # type: Dict[int, Dict[str, Any]]
    for runs in group_runs(log):
        row, scanned = scan_row(cfg, log, runs, motors)
        motor = scanned[0] if scanned else ""
        rows.append(row)
        for r in runs:
            per_run[r] = {"scan_id": runs[0], "scan_motor": motor,
                          "scan_value": motors.get(r, {}).get(motor, math.nan) if motor else math.nan}
    df = pd.DataFrame(rows, columns=SCAN_COLUMNS).set_index("scan_id")
    return df, per_run


def describe(row: Any, scan_id: int) -> str:
    """One line about a scan, for the logger's output."""
    motor = row["scan_motor_alias"] or "motor not known yet"
    vals = ""
    if _num(row["start_value"]) is not None:
        vals = " {} -> {} (step {})".format(_fmt(row["start_value"]), _fmt(row["end_value"]), _fmt(row["step_size"]))
    return "scan {}: runs {}, {} of {} steps, {}, {}{}".format(
        scan_id, row["runs"], int(row["steps_done"]), int(row["total_steps"]) if _num(row["total_steps"]) else "?",
        row["status"], motor, vals)


# ================================================================ looking up scans (session)
def select(cfg: Config, which: int = -1) -> Tuple[int, Any]:
    """(scan_id, row of the scan table) of a scan, read fresh from <output>/scans.csv (so it
    also follows an online logger running in another terminal).

    which = -1 newest scan, -2 the one before, ...; 0 the first scan, 1 the second, ...;
    a run number: the scan that run belongs to (any of its runs, not only the first)."""
    df = load_scans(scans_path(cfg))
    if not len(df):
        raise LookupError("no scans yet in {} (make_runlog() or start_online_logger())".format(scans_path(cfg)))
    which = int(which)
    from silas.core import runlog

    log = runlog.load(runlog.runlog_path(cfg))
    if which >= 0 and (which in df.index or which in log.index):   # a run number
        if which in df.index:
            return which, df.loc[which]
        sid_val = _num(log.at[which, "scan_id"])
        if sid_val is None or int(sid_val) not in df.index:
            raise LookupError("run {} is not part of a scan (it is a plain run)".format(which))
        return int(sid_val), df.loc[int(sid_val)]
    if abs(which) > 100000:
        raise LookupError("run {} is not in the run log".format(which))
    try:
        sid = int(df.index[which])
    except IndexError:
        raise IndexError("scan {} does not exist: there are {} scans (0..{} or -1..-{})".format(
            which, len(df), len(df) - 1, len(df)))
    return sid, df.loc[sid]


def run_list(cfg: Config, scan_id: int) -> List[int]:
    """All runs of a scan (in run order), from the run log."""
    from silas.core import runlog

    log = runlog.load(runlog.runlog_path(cfg))
    return [int(r) for r in log.index[log["scan_id"] == scan_id]]


def motor_text(cfg: Config, true_name: str) -> str:
    """'delay_m (xfel_bl_3_st_2_motor_1/position) [pulse]', or the true name if no alias."""
    if not true_name:
        return "not known yet (known from the second step on)"
    alias = names.display_name(cfg, true_name)
    unit = names.entry(cfg, true_name).get("unit")
    text = true_name if alias == true_name else "{} ({})".format(alias, true_name)
    return text + (" [{}]".format(unit) if unit else "")


def summary(cfg: Config, scan_id: int, row: Any) -> str:
    """Several lines about one scan, for printing / copying into a message."""
    runs = run_list(cfg, scan_id) or [int(row["first_run"])]
    lines = ["scan {}   ({}, {} of {} steps)".format(
        scan_id, row["status"], int(row["steps_done"]),
        int(row["total_steps"]) if _num(row["total_steps"]) is not None else "?")]
    lines.append("  runs       {}-{}  ({} runs{})".format(
        runs[0], runs[-1], len(runs),
        "" if runs[-1] - runs[0] + 1 == len(runs) else "; not consecutive: " + row["runs"]))
    lines.append("  motor      " + motor_text(cfg, row["scan_motor"]))
    if _num(row["start_value"]) is not None:
        lines.append("  values     {} -> {}, step {}".format(
            _fmt(row["start_value"]), _fmt(row["end_value"]), _fmt(row["step_size"])))
    if row["other_scanned"]:
        lines.append("  also moved " + row["other_scanned"])
    if row["repeated_steps"]:
        lines.append("  repeated   " + row["repeated_steps"])
    if row["missing_steps"]:
        lines.append("  missing    steps " + row["missing_steps"])
    if row["conditions"]:
        lines.append("  conditions " + row["conditions"])
    lines.append("  time       {} - {} JST".format(row["start_jst"], row["stop_jst"] or "(running)"))
    comment = str(row["comment"]).replace("\n", " | ")
    if comment:
        lines.append("  comment    " + comment)
    return "\n".join(lines)


def scan_runs(cfg: Config, which: int = -1) -> Dict[str, Any]:
    """The runs of a scan for the analysis (read fresh from the logger's files):
    {"scan_id", "motor" (alias or true name), "motor_true", "status", "runs": [{"run", "step",
    "value" (scanned motor position or None), "start_tag", "end_tag" (None while running),
    "status", "taghi"}]}. which as in select(). LookupError / IndexError if there is none."""
    from silas.core import runlog

    sid, row = select(cfg, which)
    log = runlog.load(runlog.runlog_path(cfg))
    part = log[log["scan_id"] == sid]
    runs = []  # type: List[Dict[str, Any]]
    for run, r in part.iterrows():
        status = _num(r["runstatus"])
        end = _num(r["end_tagnumber"])
        runs.append({"run": int(run), "step": _num(r["step"]), "value": _num(r["scan_value"]),
                     "start_tag": _num(r["start_tagnumber"]),
                     "end_tag": end if status == 0 and end else None,
                     "status": status, "taghi": _num(r["hightagnumber"])})
    true_name = str(row.get("scan_motor") or "")
    motor = str(row.get("scan_motor_alias") or true_name)
    return {"scan_id": int(sid), "motor": motor, "motor_true": true_name, "status": str(row.get("status", "")),
            "runs": runs}
