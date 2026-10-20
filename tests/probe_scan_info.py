#!/usr/bin/env python3
"""Probe how SACLA records scans (read-only). Run on an HPC compute node:

    qsub -I -l walltime=1:00:00 -l ncpus=2 -q serial
    module load python/SACLA_python-3.7/offline
    cd SILAS
    python tests/probe_scan_info.py 1234567 1234568 ...     # run numbers (scan runs + one plain run)
    python tests/probe_scan_info.py --bl 3 --sample 200 1234567

For every run it writes output/probe/scan_probe_<run>.json and prints a summary:
  * dbpy.read_runinfo
  * dbpy.read_run_supplementinfo (scan steps: tag ranges, scanmode, comment)
  * every condition of dbpy.read_conditionmaster() with its value for the run
  * dbpy.read_run_deviceinfo for all device categories/types, and their settings
  * point detectors that CHANGE during the run: the standard motors from the config
    and every "xfel_bl_<bl>_*motor*" equipment, sampled at --sample tags of the run
Nothing is written to SACLA; only standard library + dbpy + numpy are used.
"""
import argparse
import json
import os
import sys
from typing import Any, Dict, List

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import silas  # noqa: E402,F401  applies the profile's sys.path
import dbpy  # noqa: E402

from silas.core import standard  # noqa: E402


def _try(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except dbpy.APIError as err:
        return {"APIError": list(err.args)}
    except Exception as err:  # noqa: BLE001  report anything, never stop the probe
        return {"error": "{}: {}".format(type(err).__name__, err)}


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    return x


def changing_signals(bl: int, run: int, n_sample: int) -> Dict[str, Any]:
    """Which motors / standard signals change during the run (sampled tags)."""
    taghi = dbpy.read_hightagnumber(bl, run)
    tags = dbpy.read_taglist_byrun(bl, run)
    if len(tags) > n_sample:
        idx = np.linspace(0, len(tags) - 1, n_sample).astype(int)
        tags = tuple(int(tags[i]) for i in idx)
    names = [t for t, _, _ in standard.POINT_DETECTORS]
    eq = _try(dbpy.read_equiplist)
    if isinstance(eq, (list, tuple)):
        prefix = "xfel_bl_{}_".format(bl)
        names += sorted(e for e in eq if e.startswith(prefix) and "motor" in e and e not in names)
    out = {}  # type: Dict[str, Any]
    for name in names:
        vals = _try(dbpy.read_syncdatalist_float, name, taghi, tags)
        if isinstance(vals, dict):
            continue  # not recorded in this run
        v = np.asarray(vals, float)
        ok = np.isfinite(v)
        if not ok.any():
            continue
        uniq = np.unique(np.round(v[ok], 6))
        entry = {"n_finite": int(ok.sum()), "n_unique": int(len(uniq)),
                 "min": float(uniq.min()), "max": float(uniq.max())}  # type: Dict[str, Any]
        if len(uniq) > 1:
            # tags where the value changes (step boundaries as seen by this signal)
            changes = [int(tags[i]) for i in range(1, len(v)) if ok[i] and ok[i - 1] and v[i] != v[i - 1]]
            entry.update(unique_values=uniq[:50].tolist(), change_tags=changes[:200])
        out[name] = entry
    return {"sampled_tags": len(tags), "first_tag": int(tags[0]), "last_tag": int(tags[-1]), "signals": out}


def devices(bl: int, run: int) -> Dict[str, Any]:
    cats = _try(dbpy.read_devicecategorymaster) or []
    types = _try(dbpy.read_devicetypemaster) or []
    out = {"categories": cats, "types": types, "devices": []}  # type: Dict[str, Any]
    if not isinstance(cats, list) or not isinstance(types, list):
        return out
    for c in cats:
        for t in types:
            info = _try(dbpy.read_run_deviceinfo, bl, run, int(c["devicecategory_id"]), int(t["devicetype_id"]))
            if isinstance(info, list) and info:
                for dev in info:
                    dev = dict(dev)
                    dev["settings"] = _try(dbpy.read_run_devicesettinginfo, bl, run, int(dev["deviceid"]))
                    out["devices"].append(dev)
    return out


def conditions(bl: int, run: int) -> List[Any]:
    master = _try(dbpy.read_conditionmaster) or []
    out = []
    if isinstance(master, list):
        for c in master:
            val = _try(dbpy.read_run_conditioninfo, bl, run, int(c["conditionid"]))
            if val:
                out.append({"condition": c, "value": val})
    return out


def probe(bl: int, run: int, n_sample: int, out_dir: str) -> None:
    print("=" * 78)
    print("run", run)
    result = {"bl": bl, "run": run}  # type: Dict[str, Any]
    result["runinfo"] = _try(dbpy.read_runinfo, bl, run)
    result["supplementinfo"] = _try(dbpy.read_run_supplementinfo, bl, run)
    result["conditions"] = conditions(bl, run)
    result["devices"] = devices(bl, run)
    result["changing"] = _try(changing_signals, bl, run, n_sample)
    path = os.path.join(out_dir, "scan_probe_{}.json".format(run))
    with open(path, "w") as fh:
        json.dump(_jsonable(result), fh, indent=1)

    ri = result["runinfo"]
    if isinstance(ri, dict) and "comment" in ri:
        print("comment :", ri.get("comment"), "| tags", ri.get("start_tagnumber"), "-", ri.get("end_tagnumber"))
    sup = result["supplementinfo"]
    if isinstance(sup, list):
        print("supplementinfo: {} steps".format(len(sup)))
        for s in sup[:5]:
            print("   ", s)
        if len(sup) > 5:
            print("    ...", sup[-1])
    else:
        print("supplementinfo:", sup)
    print("conditions with a value: {}".format(len(result["conditions"])))
    for c in result["conditions"][:10]:
        print("   ", c["condition"].get("conditionname"), "=", c["value"])
    print("devices: {}".format(len(result["devices"].get("devices", []))))
    for dv in result["devices"].get("devices", [])[:10]:
        print("   ", dv.get("devicecategory"), "/", dv.get("devicetype"), dv.get("madocaobject"),
              dv.get("location"))
    ch = result["changing"]
    if isinstance(ch, dict) and "signals" in ch:
        moving = {k: v for k, v in ch["signals"].items() if v["n_unique"] > 1}
        print("signals that change ({} sampled tags): {}".format(ch["sampled_tags"], len(moving)))
        for k, v in sorted(moving.items(), key=lambda kv: -kv[1]["n_unique"])[:15]:
            print("    {:<55s} {:4d} values  {} .. {}".format(k, v["n_unique"], v["min"], v["max"]))
    print("full result:", path)


def main() -> None:
    ap = argparse.ArgumentParser(description="probe how SACLA records scans (read-only)")
    ap.add_argument("runs", type=int, nargs="+", help="run numbers")
    ap.add_argument("--bl", type=int, default=3)
    ap.add_argument("--sample", type=int, default=400, help="tags sampled per run for changing signals")
    args = ap.parse_args()
    out_dir = os.path.join(ROOT, "output", "probe")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    print("dbpy", dbpy.get_api_version())
    for run in args.runs:
        probe(args.bl, run, args.sample, out_dir)


if __name__ == "__main__":
    main()
