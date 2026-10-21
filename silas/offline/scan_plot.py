"""
"""
import argparse
import os
import time
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import silas # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import sacla, scan
from silas.core.config import Config
from silas.offline.scan_analysis import load_h5, plot

def scan_plot(args: Any) -> None:
    cfg = Config()
    sc = scan.scan_runs(cfg, args.scan)
    path = os.path.join(cfg.output_dir(), "scans", "scan_{}".format(sc["scan_id"]))
    filepath = os.path.join(path, "scan_{}".format(sc["scan_id"]) + ".h5")
    data = load_h5(filepath)

    info = data["info"]
    res = data["results"]
    tt = data.get("timetool")

    ref_units = {
        k: v.get("unit", "")
        for k, v in info["references"].items()
    }

    title = f"scan {info['scan']['scan_id']} ({len(info['runs'])} runs) - {info['array']}"

    xlabel = (
        info["scan"]["motor"]
        if res["table"] and res["table"][0]["x_is_value"]
        else "scan step (motor value not known)"
    )

    x_scale = 1.0
    if tt is not None and res["table"] and res["table"][0]["x_is_value"]:
        x_scale = info["tt_binning"]["ps_per_pulse"]
        xlabel += " x ps_per_pulse (ps)"

    if tt is not None:
        from silas.core import ttbin

        ttbin.plot_bins(
            cfg,
            tt["bins"],
            tt["panels"],
            f"{title} - time-tool bins of {tt['info']['bin_ps']:g} ps",
            path=None,
            show=True,
            off=tt["off"],
        )
    else:
        plot(
            res,
            title,
            xlabel,
            path=None,
            show=True,
            ref_units=ref_units,
            x_scale=x_scale,
        )
    
@sacla.requires_sacla
def run_live(args: Any) -> None:
    scan_plot(args)

def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="offline scan plot: ROI quantiles per step")
    ap.add_argument("--scan", type=int, default=-1, help="-1 newest (default), -2, 0, 1, ... or a run number")
    run_live(ap.parse_args(argv))

if __name__ == "__main__":
    main()