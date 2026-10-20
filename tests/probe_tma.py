#!/usr/bin/env python3
"""What can tmapy (TMAUserAPI) give us? Arrival-timing monitor results (HPC compute node, read-only).

    cd ~/SILAS && bt_env
    python tests/probe_tma.py                                  # connection, groups, TMA-IDs
    python tests/probe_tma.py --run 1234501                    # + the run's cameras and its TMA results
    python tests/probe_tma.py --run 1234501 --tmaid 3          # one TMA-ID only
    python tests/probe_tma.py --all-groups                     # also the groups you may read

tmapy does not analyse anything: it reads the per-tag results that SACLA's TMA tools wrote into
the User-Database (UDB) under a TMA-ID (TMAOnlineCUI/GUI live at the beamline,
TimingMonitorAnalyzerCUI/GUI offline). This probe shows step by step:

  1. tmapy version and the range of analysis IDs reserved for TMA;
     with --run: the run's high tag and tags, and the image detectors recorded in it (is there a
     timing camera: OPAL / IPX / CHET?) - dbpy only, works without the UDB
  2. the connection to the UDB (needs an authentication file: udb_MakeAuthFile; ~/.udb is listed)
  3. your UDB group, the groups you may read, and the TMA-IDs in them (online / offline server)
  5. with --run: for every TMA-ID (or --tmaid) the results inside the run's tags, read as a list
     (open_udbdata_list / retrieve_udbdata): how many tags, how many valid, the edges (pixels),
     the quality flags, and the reading speed; the analysis parameters (camera, tag increment)
     at the first result; the same tags read one by one (collect_udbdata) for the speed

Results are written to output/probe/tma_<run>_<tmaid>.csv. Nothing is written to the UDB.
"""
import argparse
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import silas  # noqa: E402,F401  applies the profile's sys.path
from silas.core import db  # noqa: E402
from silas.core.config import Config  # noqa: E402

CAMERAS = ("OPAL", "IPX", "CHET")      # timing-monitor cameras (SACLA detector_category: OPAL*, IPX*, CHET*)


def const(mod: Any, name: str, default: int) -> int:
    """A tmapy constant (not in our type stub)."""
    return int(getattr(mod, name, default))


def err(e: Exception) -> str:
    a = list(getattr(e, "args", ()))  # type: List[Any]
    return "{} {}".format(a[0], a[1]) if len(a) >= 2 else str(e)


def tma_ids(tmapy: Any, ctrl: Any) -> Dict[int, str]:
    """{TMA-ID: server type} in the current group."""
    kinds = {const(tmapy, "TMA_UDB_SERVER_TYPE_ONLINE", 1): "online",
             const(tmapy, "TMA_UDB_SERVER_TYPE_OFFLINE", 2): "offline",
             const(tmapy, "TMA_UDB_SERVER_TYPE_BOTH", 3): "both"}
    out = {}  # type: Dict[int, str]
    try:
        ids = ctrl.get_tmaid_list(const(tmapy, "TMA_UDB_SERVER_TYPE_BOTH", 3))
    except tmapy.APIError as e:
        print("     get_tmaid_list: {}".format(err(e)))
        return out
    for i in ids:
        try:
            out[int(i)] = kinds.get(int(ctrl.get_tmaid_servertype(int(i))), "?")
        except tmapy.APIError as e:
            out[int(i)] = "? ({})".format(err(e))
    return out


def read_results(tmapy: Any, ctrl: Any, tmaid: int, taghi: int, first: int, last: int) \
        -> Tuple[List[Dict[str, Any]], float]:
    """All results in [first, last] as a list, and the seconds it took."""
    buf = tmapy.UdbDataBuffer()
    rows = []  # type: List[Dict[str, Any]]
    end = const(tmapy, "TMA_ERR_END_RETRIEVE", 1)
    t = time.perf_counter()
    ctrl.open_udbdata_list(tmaid, taghi, first, last)
    try:
        while True:
            try:
                tag, valid = ctrl.retrieve_udbdata(buf)
            except tmapy.APIError as e:
                if e.args and e.args[0] == end:
                    break
                raise
            row = {"tag": int(tag), "valid": int(valid), "run": None, "edge": float("nan"),
                   "edge_deriv": float("nan"), "edge_fit": float("nan"), "good_deriv": -1,
                   "good_fit": -1}  # type: Dict[str, Any]
            for key, fn in (("run", "readdata_runnumber"), ("edge", "readdata_timingedge"),
                            ("edge_deriv", "readdata_timingedge_deriv"), ("edge_fit", "readdata_timingedge_fit"),
                            ("good_deriv", "readdata_qualitycheck_isgoodtotal_deriv"),
                            ("good_fit", "readdata_qualitycheck_isgoodtotal_fit")):
                try:
                    row[key] = getattr(buf, fn)()
                except tmapy.APIError:
                    pass                      # e.g. TMA_ERR_EDGE_NOT_GOOD: stays NaN / -1
            rows.append(row)
    finally:
        ctrl.close_udbdata_list()
    return rows, time.perf_counter() - t


def main() -> None:
    ap = argparse.ArgumentParser(description="probe tmapy: arrival-timing monitor results in the UDB")
    ap.add_argument("--run", type=int, help="a run to look up (its tags, cameras and TMA results)")
    ap.add_argument("--tmaid", type=int, nargs="*", help="only these TMA-IDs (default: all found)")
    ap.add_argument("--group", help="UDB group to read (default: yours)")
    ap.add_argument("--all-groups", action="store_true", help="list the TMA-IDs of every group you may read")
    ap.add_argument("--one-by-one", type=int, default=200, help="tags read with collect_udbdata for the speed")
    args = ap.parse_args()
    cfg = Config()

    # ---------------------------------------------------------------- 1. module
    import tmapy

    print("1. tmapy {}  (TMA analysis IDs reserved: {})".format(
        ".".join(str(v) for v in tmapy.get_api_version()), tmapy.get_tmaid_range()))

    # ---------------------------------------------------------------- the run (dbpy only)
    bl = cfg.beamline
    taghi = 0
    tags = []  # type: List[int]
    if args.run is not None:
        taghi = db.high_tag(bl, args.run)
        tags = list(db.tags(bl, args.run))
        dets = list(db.run_detectors(bl, args.run))
        cams = [d for d in dets if d.upper().startswith(CAMERAS)]
        print("   run {}: high tag {}, tags {} .. {} ({}), detectors: {}".format(
            args.run, taghi, tags[0], tags[-1], len(tags), ", ".join(dets)))
        print("   timing camera(s) recorded: {}".format(
            ", ".join(cams) if cams else "none found (no OPAL / IPX / CHET detector in this run)"))
        print("   (with a camera, the timing can always be analysed offline: TimingMonitorAnalyzerCUI)")

    # ---------------------------------------------------------------- 2. connection
    udb_dir = os.path.expanduser("~/.udb")
    print("   ~/.udb: {}".format(", ".join(sorted(os.listdir(udb_dir))) if os.path.isdir(udb_dir)
                                  else "does not exist (no UDB authentication file yet)"))
    try:
        ctrl = tmapy.UdbCtrlObject()
    except tmapy.APIError as e:
        print("2. no connection to the User-Database: {}".format(err(e)))
        print("   The UDB has its own accounts (UDB user name + password, registered by your group's UDB Use")
        print("   Representative). With them, make the authentication file once: udb_MakeAuthFile on a compute")
        print("   node, or 'Create UDB-auth file' in the Offline-UDB Web (SACLA HPC portal); then run this again.")
        return
    print("2. connected to the User-Database")

    # ---------------------------------------------------------------- 3. groups and TMA-IDs
    mine = ctrl.get_default_groupname()
    readable = list(ctrl.get_readonly_groupname_list())
    print("3. your group: {}   readable groups: {}".format(mine, ", ".join(readable) or "none"))
    groups = [args.group] if args.group else ([mine] + [g for g in readable if g != mine]
                                              if args.all_groups else [mine])
    found = {}  # type: Dict[Tuple[str, int], str]
    for g in groups:
        try:
            ctrl.change_group(g)
        except tmapy.APIError as e:
            print("   group {}: {}".format(g, err(e)))
            continue
        ids = tma_ids(tmapy, ctrl)
        print("   group {}: {} TMA-IDs {}".format(g, len(ids), ", ".join("{} ({})".format(i, k)
                                                                     for i, k in sorted(ids.items()))))
        for i, k in ids.items():
            found[(g, i)] = k
    if args.run is None:
        print("\n(--run N looks up a run's TMA results)")
        return

    # ---------------------------------------------------------------- 5. results
    wanted = [(g, i) for (g, i) in sorted(found) if not args.tmaid or i in args.tmaid]
    if args.tmaid and not wanted:
        wanted = [(groups[0], i) for i in args.tmaid]
    if not wanted:
        print("5. no TMA-ID to read (none in the group; --tmaid N, --group G or --all-groups)")
        return
    out = os.path.join(cfg.output_dir(), "probe")
    if not os.path.isdir(out):
        os.makedirs(out)
    for g, tmaid in wanted:
        ctrl.change_group(g)
        print("5. TMA-ID {} (group {}):".format(tmaid, g))
        try:
            rows, dt = read_results(tmapy, ctrl, tmaid, taghi, tags[0], tags[-1])
        except tmapy.APIError as e:
            print("   reading failed: {}".format(err(e)))
            continue
        valid = [r for r in rows if r["valid"] == 1]
        print("   {} results for the run's {} tags, {} with valid_flag 1; read as a list in {:.2f} s ({:.0f} "
              "tags/s)".format(len(rows), len(tags), len(valid), dt, len(rows) / max(dt, 1e-9)))
        if not rows:
            continue
        flags = {}  # type: Dict[int, int]
        for r in rows:
            flags[r["valid"]] = flags.get(r["valid"], 0) + 1
        print("   valid_flag counts: {}".format(", ".join("{}: {}".format(k, v) for k, v in sorted(flags.items()))))
        for r in rows[:5]:
            print("   tag {tag}  valid {valid}  run {run}  edge {edge:.2f}  deriv {edge_deriv:.2f}  fit "
                  "{edge_fit:.2f}  good deriv/fit {good_deriv}/{good_fit}".format(**r))
        edges = sorted(float(r["edge_deriv"]) for r in valid if r["edge_deriv"] == r["edge_deriv"])
        if edges:
            n_e = len(edges) - 1
            print("   derivative edge of the valid tags: median {:.1f} px, 16-84 %: {:.1f} .. {:.1f} px".format(
                edges[len(edges) // 2], edges[int(n_e * 0.1587)], edges[int(n_e * 0.8413)]))
        par = tmapy.UdbParamBuffer()
        try:
            ctrl.collect_udbparam(par, tmaid, taghi, rows[0]["tag"])
            print("   analysis parameters: camera {}, tag increment {}, host {}, line profile {}".format(
                par.readparam_detid(), par.readparam_tagincrement(), par.readparam_hostname(),
                par.readparam_lineprofile()))
        except tmapy.APIError as e:
            print("   analysis parameters: {}".format(err(e)))
        n = min(args.one_by_one, len(rows))
        buf = tmapy.UdbDataBuffer()
        t = time.perf_counter()
        for r in rows[:n]:
            ctrl.collect_udbdata(buf, tmaid, taghi, r["tag"])
        dt1 = time.perf_counter() - t
        print("   one by one (collect_udbdata): {} tags in {:.2f} s ({:.0f} tags/s)".format(
            n, dt1, n / max(dt1, 1e-9)))
        path = os.path.join(out, "tma_{}_{}.csv".format(args.run, tmaid))
        with open(path, "w") as fh:
            keys = ["tag", "valid", "run", "edge", "edge_deriv", "edge_fit", "good_deriv", "good_fit"]
            fh.write(",".join(keys) + "\n")
            for r in rows:
                fh.write(",".join(str(r[k]) for k in keys) + "\n")
        print("   written: {}".format(path))


if __name__ == "__main__":
    main()
