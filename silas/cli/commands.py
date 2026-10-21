"""Commands exposed in the interactive session.

Everything that talks to dbpy is marked @requires_sacla: in smoke mode it is
not executed, this file is smoke-tested instead.
"""
import fnmatch
import os
import functools
from typing import Any, Callable, Dict, List, Optional

from silas.core import (bootstrap, db, energy, envcheck, mpccd, names, roi, runlog, runner, sacla, scan,
                           scanroi, standard, timetool)
from silas.core.config import Config


def _bind(cfg: Config, func: Callable[..., Any]) -> Callable[..., Any]:
    """``func(cfg, ...)`` as ``f(...)``, keeping name and docstring."""
    @functools.wraps(func)
    def bound(*args: Any, **kwargs: Any) -> Any:
        return func(cfg, *args, **kwargs)
    return bound


def build(cfg: Config) -> Dict[str, Any]:
    """{name: command} for the session namespace."""

    # ------------------------------------------------------------ run log
    def start_online_logger(poll: float = 5.0) -> Optional[int]:
        """Start the online run logger in a new terminal (follows new runs)."""
        return runner.run("silas.online.logger", "--poll", str(poll), new_terminal=True)

    def make_runlog(rebuild: bool = False, last_run: Optional[int] = None,
                    workers: int = runner.OFFLINE_WORKERS) -> Optional[int]:
        """(Re)create the run log from first_run in this terminal (offline logger), with
        `workers` processes reading the runs (default 6; at most the job's cores)."""
        args = ["--workers", str(workers)]  # type: List[str]
        if rebuild:
            args.append("--rebuild")
        if last_run is not None:
            args += ["--last-run", str(last_run)]
        elif cfg.get("last_run", None) is not None:
            args += ["--last-run", str(cfg.get("last_run"))]
        return runner.run("silas.offline.logger", *args)

    def show_runlog(last: Optional[int] = 20) -> Any:
        """The run log as a table (the last ``last`` runs; None = all). Reads the file only."""
        df = runlog.load(runlog.runlog_path(cfg))
        if not len(df):
            print("run log is empty: {}".format(runlog.runlog_path(cfg)))
        return df if last is None else df.tail(last)

    # ------------------------------------------------------------ scans (read the logger's files)
    # which: -1 newest scan, -2 the one before, ...; 0 first scan of the beamtime, 1 second, ...;
    #        or a run number: the scan that run belongs to.
    def show_scans(last: Optional[int] = 20, full: bool = False) -> Any:
        """The scan table (the last ``last`` scans; None = all). full=False: the main columns;
        full=True: all columns (start/end/step size, repeated and missing steps, ...)."""
        df = scan.load_scans(scan.scans_path(cfg))
        if not len(df):
            print("no scans yet: {} (run make_runlog() or start_online_logger())".format(scan.scans_path(cfg)))
            return df
        if not full:
            df = df.copy()
            df["motor"] = [names.display_name(cfg, m) if m else "?" for m in df["scan_motor"]]
            df["steps"] = ["{}/{}".format(int(a), int(b) if b == b else "?")
                           for a, b in zip(df["steps_done"], df["total_steps"])]
            df = df[["runs", "n_runs", "steps", "status", "motor", "start_value", "end_value",
                     "step_size", "start_jst"]]
        return df if last is None else df.tail(last)

    def scan_info(which: int = -1) -> Dict[str, Any]:
        """Print a summary of a scan (default: the newest) and return its row as a dict."""
        sid, row = scan.select(cfg, which)
        print(scan.summary(cfg, sid, row))
        return dict(row, scan_id=sid)

    def scan_range(which: int = -1) -> Any:
        """(first run, last run) of a scan (default: the newest); prints it in forms that can
        be copied to someone analysing elsewhere."""
        sid, row = scan.select(cfg, which)
        runs = scan.run_list(cfg, sid) or [int(row["first_run"])]
        print("scan {}: runs {}-{}   ({} runs, {})".format(sid, runs[0], runs[-1], len(runs), row["status"]))
        print("    np.arange({}, {}, 1)".format(runs[0], runs[-1] + 1))
        if runs[-1] - runs[0] + 1 != len(runs):
            print("    not consecutive, the scan's runs are: {}".format(row["runs"]))
        return runs[0], runs[-1]

    def scan_run_list(which: int = -1) -> List[int]:
        """All runs of a scan (default: the newest) as a list (also when not consecutive)."""
        sid, _ = scan.select(cfg, which)
        return scan.run_list(cfg, sid)

    def scan_motor(which: int = -1) -> Any:
        """(alias, true name) of the motor scanned in a scan (default: the newest); prints it with
        its unit. alias = true name if no alias is set; ("", "") while not known yet."""
        sid, row = scan.select(cfg, which)
        true_name = row["scan_motor"]
        print("scan {}: {}".format(sid, scan.motor_text(cfg, true_name)))
        if row["other_scanned"]:
            print("    also moved: {}".format(row["other_scanned"]))
        return (names.display_name(cfg, true_name) if true_name else "", true_name)

    def scan_runs(which: int = -1) -> Any:
        """The runs of a scan (default: the newest) from the run log: step, motor value, status,
        tags. Repeated steps appear as several runs with the same step."""
        sid, row = scan.select(cfg, which)
        log = runlog.load(runlog.runlog_path(cfg))
        part = log[log["scan_id"] == sid]
        cols = ["step", "total_steps", "scan_value", "status", "total_tagnumber", "start_jst", "moving"]
        out = part[cols].copy()
        motor = row["scan_motor"]
        if motor:
            out = out.rename(columns={"scan_value": names.label(cfg, motor)})
        return out

    # ------------------------------------------------------------ looking around (dbpy)
    @sacla.requires_sacla
    def newest_runs(n: int = 10) -> Any:
        """Table of the n newest runs of the beamline (straight from dbpy, not the log)."""
        import pandas as pd

        bl = cfg.beamline
        newest = db.newest_run(bl)
        rows = []
        for r in range(newest, newest - 10 * n, -1):
            row = runlog.record(bl, r)
            if row is not None:
                rows.append(row)
            if len(rows) >= n:
                break
        return pd.DataFrame(rows, columns=runlog.COLUMNS).set_index("run")

    @sacla.requires_sacla
    def run_summary(run: Optional[int] = None) -> Dict[str, Any]:
        """Run info, tag range, high tag and image detectors of a run (default: newest)."""
        bl = cfg.beamline
        run = db.newest_run(bl) if run is None else run
        info = db.run_info(bl, run)
        info["status"] = db.RUN_STATUS.get(db.run_status(bl, run))
        info["tag_range"] = db.tag_range(bl, run)
        info["detector_list"] = db.run_detectors(bl, run)
        return info

    @sacla.requires_sacla
    def run_detectors(run: Optional[int] = None) -> Any:
        """Image detectors (MPCCD, cameras) recorded in a run (default: newest)."""
        bl = cfg.beamline
        return db.run_detectors(bl, db.newest_run(bl) if run is None else run)

    @sacla.requires_sacla
    def find_equipment(pattern: str = "*") -> List[str]:
        """Point detectors / equipment matching a wildcard, e.g. find_equipment("*bl_3*motor*")."""
        return [e for e in db.equipment() if fnmatch.fnmatch(e, pattern)]

    @sacla.requires_sacla
    def newest_tag(name: Optional[str] = None) -> Any:
        """(high tag, tag) newest for a point detector (alias or true name; default:
        first configured point detector)."""
        ids = names.point_detector_ids(cfg)
        if name is None and not ids:
            raise ValueError("no point detector given and none configured")
        return db.newest_tag(names.resolve(cfg, name or ids[0]))

    @sacla.requires_sacla
    def scan_overview(first_run: int, last_run: Optional[int] = None,
                      motors: Optional[List[str]] = None) -> Any:
        """One row per run: SACLA step number / total steps, comment, and the value of every
        motor (default: configured point detectors with 'motor' in the name); prints which
        motors change between runs (= scanned). SACLA makes one run per scan step."""
        equip = [names.resolve(cfg, m) for m in motors] if motors else None
        last_run = last_run or cfg.get("last_run", None)
        return scan.overview(cfg, first_run, last_run, equip)

    @sacla.requires_sacla
    def read_point(name: str, run: Optional[int] = None, n_tags: Optional[int] = None) -> Any:
        """Values of a point detector (alias or true name) in a run as a pandas Series
        indexed by tag (n_tags: only the first n tags)."""
        bl = cfg.beamline
        run = db.newest_run(bl) if run is None else run
        tag_list = db.tags(bl, run)
        if n_tags is not None:
            tag_list = tag_list[:n_tags]
        true = names.resolve(cfg, name)
        df = db.read_run_points(bl, run, [true], tag_list)
        return df[true].rename(names.label(cfg, true))

    # ------------------------------------------------------------ moving the code
    def sync_to_hpc(go: bool = False, delete: bool = False, host: str = "sacla_HPC",
                    dest: str = "SILAS", user: Optional[str] = None) -> int:
        """Copy SILAS to the HPC (scripts/sync_to_hpc.sh). go=False: dry run only.
        config/ and output/ on the HPC are never touched. host: the ssh alias (~/.ssh/config);
        user="name": without the alias, ssh to name@xhpcfep.hpc.spring8.or.jp."""
        import subprocess

        script = os.path.join(bootstrap.PACKAGE_ROOT, "scripts", "sync_to_hpc.sh")
        args = ["bash", script, "--dest", dest] + (["--user", user] if user else ["--host", host])
        if go:
            args.append("--go")
        if delete:
            args.append("--delete")
        return subprocess.call(args)

    # ------------------------------------------------------------ MPCCD array
    def _use_test(test: Optional[bool]) -> bool:
        """Default: test data on the local profile, the real detector elsewhere."""
        return bootstrap.STATE["profile"] == "local" if test is None else bool(test)

    def start_mpccd_viewer(workers: Optional[int] = None, test: Optional[bool] = None,
                           name: Optional[str] = None, run: Any = None, replay: bool = False,
                           rate: Optional[float] = None, max_tags: Optional[int] = None,
                           every: Optional[int] = None, cache: Optional[str] = None) -> Optional[int]:
        """The MPCCD viewer: live (online) or of stored runs (offline, HPC). Same window and keys.
        Online (no run): new terminal, MPI 1 display + `workers` readers (default 4; 0: one
        process); test=True: test data (default on the local profile).
        Offline (run=N or [N, M, ...]): all shots loaded once by `workers` processes (default 6)
        (max_tags / every: fewer) into RAM or /work (cache="auto" / "ram" / "work"), the
        average shown, pump on / off with w; dark and threshold changes recompute it.
        replay=True: play the (first) run as if live, at `rate` shots per second (default 10).
        Options of the other kind are ignored, with a message."""
        runs = [] if run is None else ([int(run)] if isinstance(run, int) else [int(r) for r in run])
        offline = bool(runs) and not replay
        if workers is None:
            workers = runner.OFFLINE_WORKERS if offline else 4
        use_test = _use_test(test) if not runs else bool(test)
        key, _ = mpccd.get_array(cfg, name)
        if name and mpccd.default_array(cfg) != key:
            mpccd.set_default_array(cfg, key)
            print("default MPCCD array is now {}: add_roi, set_threshold, ... without name= "
                  "use it (use_mpccd to change)".format(mpccd.label(cfg, key)))
        args = ["--array", key, "--workers", str(workers)] + (["--test"] if use_test else [])
        if runs:
            args += ["--run"] + [str(r) for r in runs]
        if replay:
            args.append("--replay")
        for opt, value in (("--rate", rate), ("--max-tags", max_tags), ("--every", every), ("--cache", cache)):
            if value is not None:
                args += [opt, str(value)]       # the viewer says which ones it ignores
        launcher = None
        if workers > 0 and not offline:
            launcher = runner.mpi_launcher(workers + 1)
            if launcher is None:
                print("mpi4py or mpirun not available here: one process reads and displays")
        return runner.run("silas.online.mpccd_view", *args, new_terminal=True, launcher=launcher,
                          execute=use_test and not runs)

    def capture_dark(shots: int = 300, test: Optional[bool] = None, name: Optional[str] = None) -> Optional[int]:
        """Average `shots` shots WITHOUT X-rays (shutter closed) into the array's dark file."""
        use_test = _use_test(test)
        key, _ = mpccd.get_array(cfg, name)
        args = ["--array", key, "--shots", str(shots)] + (["--test"] if use_test else [])
        rc = runner.run("silas.online.dark", *args, execute=use_test)
        cfg.reload()
        return rc

    def set_dark(run: Optional[int] = None, file: Optional[str] = None, shots: str = "all",
                 workers: int = runner.OFFLINE_WORKERS, max_shots: Optional[int] = None,
                 name: Optional[str] = None) -> Optional[int]:
        """The MPCCD dark from a stored run (HPC): run=N averages the run's raw shots with `workers`
        processes (shots="all", default: every shot of a run taken with the X-rays blocked;
        shots="xray_off": only shots with the X-ray shutter closed), saves the dark file and sets it
        (dark subtraction on). file=...: use a saved dark file instead. Live at the beamline:
        capture_dark(). name: the array (default: the default array)."""
        rc = None  # type: Optional[int]
        if file:
            path = os.path.abspath(os.path.expanduser(file))
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            mpccd.set_option(cfg, "corrections.dark_file", path, name)
            mpccd.set_option(cfg, "corrections.dark", True, name)
            print("dark file set: {} (dark subtraction on)".format(path))
        if run is not None:
            if shots not in ("all", "xray_off"):
                raise ValueError('shots must be "all" or "xray_off"')
            args = ["--run", str(int(run)), "--workers", str(int(workers))]
            if name:
                args += ["--array", name]
            if max_shots:
                args += ["--max-shots", str(int(max_shots))]
            if shots == "xray_off":
                args.append("--xray-off")
            rc = runner.run("silas.offline.dark", *args)
            cfg.reload()
        return rc

    def set_mpccd(key: str, value: Any, name: Optional[str] = None) -> None:
        """Change an MPCCD array setting, e.g. set_mpccd("display.cmap", "magma").
        A running viewer follows within a second."""
        mpccd.set_option(cfg, key, value, name)

    # ------------------------------------------------------------ time tool
    def start_tt_viewer(run: Optional[int] = None, test: Optional[bool] = None, workers: int = 4,
                        camera: Optional[str] = None) -> Optional[int]:
        """The time-tool viewer: single shots of the timing camera, the ROI and its trace.
        Live (no run): new terminal, MPI 1 display + `workers` readers making the trace of
        every shot (0: one process); test=True: a made-up camera (default on the local profile).
        Offline (run=N, HPC): step through the run's shots (arrows, g: go to, f: all / timing /
        background shots). camera: detector ID (default: set_tt_camera, else the first OPAL)."""
        use_test = _use_test(test) if run is None else False
        args = []  # type: List[str]
        if run is not None:
            args += ["--run", str(int(run))]
        if use_test:
            args.append("--test")
        if camera:
            args += ["--camera", str(camera)]
        launcher = None
        if run is None and workers > 0:
            launcher = runner.mpi_launcher(workers + 1)
            if launcher is None:
                print("mpi4py or mpirun not available here: one process reads and displays")
        return runner.run("silas.online.tt_view", *args, new_terminal=True, launcher=launcher,
                          execute=use_test and run is None)

    def set_tt_roi(x0: Optional[int] = None, x1: Optional[int] = None, y0: Optional[int] = None,
                   y1: Optional[int] = None) -> None:
        """Time-tool ROI in camera pixels, inclusive (x = column, y = row); no arguments: the
        whole image. Or draw it in the viewer (key e)."""
        timetool.set_roi(cfg, x0, x1, y0, y1)
        print(timetool.describe(cfg))

    def set_tt_binning(binning: str = "rows") -> None:
        """"rows": mean over the ROI's rows -> trace along x; "cols": mean over its columns ->
        trace along y. Key b in the viewer."""
        timetool.set_option(cfg, "binning", binning)
        print(timetool.describe(cfg))

    def set_tt_background(run: Optional[int] = None, mode: Optional[str] = None, file: Optional[str] = None,
                          clear: bool = False, workers: int = runner.OFFLINE_WORKERS,
                          max_shots: Optional[int] = None, shots: str = "all") -> Optional[int]:
        """The time-tool background: the mean image of a background run's shots.
        run=N: made from a stored run (HPC) by `workers` processes; shots="all" (default): all
        its shots, as SACLA's tool does (check in the viewer that they have no edge); shots="background":
        only those with the X-ray shutter closed and the laser on. file=...: use a saved one.
        mode="divide" / "subtract": how each shot's trace is corrected. clear=True: none.
        A running viewer follows."""
        rc = None  # type: Optional[int]
        if clear:
            timetool.set_option(cfg, "background.file", None)
            timetool.set_option(cfg, "background.source", None)
        if file:
            path = os.path.abspath(os.path.expanduser(file))
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            timetool.set_option(cfg, "background.file", path)
            timetool.set_option(cfg, "background.source", os.path.basename(path))
        if mode is not None:
            timetool.set_option(cfg, "background.mode", mode)
        if run is not None:
            args = ["--run", str(int(run)), "--workers", str(workers)]
            if max_shots:
                args += ["--max-shots", str(int(max_shots))]
            if shots == "background":
                args.append("--only-xray-off")
            elif shots != "all":
                raise ValueError('shots must be "all" or "background"')
            rc = runner.run("silas.online.tt_background", *args)
            cfg.reload()
        print(timetool.describe(cfg))
        return rc

    def capture_tt_background(shots: int = 300, only_xray_off: bool = False,
                              test: Optional[bool] = None) -> Optional[int]:
        """Live (beamline): average `shots` shots into the time-tool background. Block the X-rays
        first: all shots are taken, unless only_xray_off=True (then only those with the X-ray
        shutter closed and the laser on, once their flags reach the SyncDB)."""
        use_test = _use_test(test)
        args = ["--shots", str(int(shots))] + (["--test"] if use_test else [])
        if only_xray_off:
            args.append("--only-xray-off")
        rc = runner.run("silas.online.tt_background", *args, execute=use_test)
        cfg.reload()
        print(timetool.describe(cfg))
        return rc

    def set_tt_camera(det_id: Optional[str] = None) -> None:
        """The time-tool camera's detector ID; None: the first OPAL / IPX / CHET of the run."""
        timetool.set_option(cfg, "detector", det_id)
        print(timetool.describe(cfg))

    def set_tt_laser(detector: str = "none", on_value: float = 1) -> None:
        """The point detector telling that the time-tool laser is on, and its value for on.
        "none" (default): no flag, the laser is taken as always on (when the time-tool probe
        does not go through the pump shutter)."""
        if detector and detector.lower() != timetool.NO_LASER_FLAG:
            names.resolve(cfg, detector)
        timetool.set_option(cfg, "laser_detector", detector or timetool.NO_LASER_FLAG)
        timetool.set_option(cfg, "laser_on_value", on_value)
        print(timetool.describe(cfg))

    def set_tt_edge(direction: Optional[str] = None, window: Any = "keep", smooth_px: Optional[float] = None,
                    fit: Optional[bool] = None, fit_range_px: Optional[int] = None, min_snr: Optional[float] = None,
                    min_step: Optional[float] = None, max_diff_px: Optional[float] = None) -> None:
        """Edge finding: direction "rising" / "falling" / "any" (in the corrected trace);
        window=(p0, p1) camera pixels searched (None: the whole trace); smooth_px: sigma of the
        Gaussian for the derivative; fit on / off and its range (+- px); the cuts for a good
        edge: min_snr, min_step, max_diff_px (fit vs derivative). Only the ones given change."""
        if window != "keep":
            if window is not None:
                p0, p1 = sorted(float(v) for v in window)
                window = [p0, p1]
            timetool.set_option(cfg, "edge.window", window)
        for key, value in (("direction", direction), ("smooth_px", smooth_px), ("fit", fit),
                           ("fit_range_px", fit_range_px), ("min_snr", min_snr), ("min_step", min_step),
                           ("max_diff_px", max_diff_px)):
            if value is not None:
                timetool.set_option(cfg, "edge." + key, value)
        print(timetool.describe(cfg))

    def _run_tt(module: str, args: List[str], workers: int, mpi: bool) -> Optional[int]:
        """Run a time-tool batch script: mpi=True under mpirun with 1 + workers processes (rank 0
        hands out the work), falling back to a pool if mpirun cannot start; else (default) a
        pool of `workers` processes. Both print a line of progress per part of a run."""
        if mpi and workers > 1:
            launcher = runner.mpi_launcher(workers + 1)
            if launcher is None:
                print("mpi4py or mpirun not available here: a pool of {} processes instead".format(workers))
            else:
                print("starting: {} python -m {} ...".format(" ".join(launcher), module), flush=True)
                rc = runner.run(module, *args, launcher=launcher)
                if rc != 255:
                    return rc
                print("mpirun could not start the processes (exit 255; test it in this shell with\n"
                      "  {} python -c \"print(1)\"\n): running with a pool of {} processes instead".format(
                          " ".join(runner.mpi_launcher(2) or ["mpirun", "-n", "2"]), workers), flush=True)
        return runner.run(module, *args)

    def calibrate_tt(which: int = -1, runs: Any = None, motor: Optional[str] = None, unit: Optional[str] = None,
                     order: int = 1, min_good: int = 20, workers: int = runner.OFFLINE_WORKERS,
                     max_tags: Optional[int] = None, show: bool = True, save: bool = True,
                     mpi: bool = False) -> Optional[int]:
        """Time-tool calibration from a delay scan of the time-tool motor (HPC, stpy): per step
        (run) the edges of all good timing shots (histograms), their median and 16-84 % range
        against the motor's set value, and a polynomial of order `order`, motor = P(edge px),
        stored as timetool.calibration (save=False: not stored); from then on every shot's
        correction is P(edge) - P(0) in the motor's units (pulse). `workers` processes (a pool;
        mpi=True: under mpirun); a line of progress per part of a run. which: the scan as in
        scan_info (-1 newest, a run number, ...); or runs=[...] / range(...) without the run
        log. motor: the set value's point detector (default: the scan's motor, else
        xfel_bl_3_st_1_motor_73/position); unit: e.g. "mm" if the config has none.
        Results: <output>/timetool/calibration_<runs>_<time>/."""
        args = ["--workers", str(workers), "--order", str(int(order)), "--min-good", str(int(min_good))]
        if runs is not None:
            rl = [int(r) for r in ([runs] if isinstance(runs, int) else runs)]
            args += ["--runs"] + [str(r) for r in rl]
        else:
            args += ["--scan", str(int(which))]
        for opt, value in (("--motor", motor), ("--unit", unit), ("--max-tags", max_tags)):
            if value is not None:
                args += [opt, str(value)]
        if show:
            args.append("--show")
        if not save:
            args.append("--no-save")
        rc = _run_tt("silas.offline.tt_calibration", args, workers, mpi)
        cfg.reload()
        return rc

    def compare_tt_sacla(which: int = -1, runs: Any = None, sacla_dir: Optional[str] = None,
                         workers: int = runner.OFFLINE_WORKERS, max_tags: Optional[int] = None,
                         show: bool = True, mpi: bool = False) -> Optional[int]:
        """Our time-tool edges (current settings) against SACLA's TimingMonitorAnalyzer results
        (<sacla_dir>/<run>.csv and .tma.h5), shot by shot: differences per run and overall,
        plots; with a calibration also the corrected timing per shot, ours (scanned motor +
        P(edge) - P(0)) against a linear calibration of SACLA's (csv column 3 - 0.0026 / 0.00666 x
        fitted edge) for the first two runs. sacla_dir: default the config's timetool.sacla_dir
        (cfg.set("timetool.sacla_dir", <folder>) once). `workers` processes (a pool; mpi=True: under mpirun), a line of
        progress per part of a run. which: a scan as in scan_info; or runs=N / [..] / range(..).
        Results: <output>/timetool/compare_<runs>_<time>/."""
        args = ["--workers", str(workers)] + (["--sacla-dir", str(sacla_dir)] if sacla_dir else [])
        if runs is not None:
            rl = [int(r) for r in ([runs] if isinstance(runs, int) else runs)]
            args += ["--runs"] + [str(r) for r in rl]
        else:
            args += ["--scan", str(int(which))]
        if max_tags:
            args += ["--max-tags", str(int(max_tags))]
        if show:
            args.append("--show")
        return _run_tt("silas.offline.tt_compare", args, workers, mpi)

    def start_tt_logger(test: Optional[bool] = None, workers: int = 2) -> Optional[int]:
        """Online time-tool log (beamline): the edge of every new tag, written run by run to
        <output>/timetool/edges/run_<run>.csv while the runs are taken. New terminal; MPI: 1
        writer + `workers` readers (0: one process). test=True: the test camera."""
        use_test = _use_test(test)
        launcher = None
        if workers > 0:
            launcher = runner.mpi_launcher(workers + 1)
            if launcher is None:
                print("mpi4py or mpirun not available here: one process reads and writes")
        return runner.run("silas.online.tt_logger", *(["--test"] if use_test else []), new_terminal=True,
                          launcher=launcher, execute=use_test)

    def make_tt_log(first_run: Optional[int] = None, last_run: Optional[int] = None, follow: bool = False,
                    rebuild: bool = False, workers: int = runner.OFFLINE_WORKERS,
                    redo_changed: bool = False) -> Optional[int]:
        """Time-tool log (HPC, stpy) in this terminal: the edge of every tag of every finished run
        from first_run (config or first_run=), written run by run like the run logger; runs done
        with other settings are kept (redo_changed=True: done again). It continues where the last
        one stopped; rebuild=True starts from scratch. last_run=N: stop after run N; follow=True: keep
        following new runs (with last_run: until run N is done). For a batch job:
        qsub -v last_run=N ~/SILAS/scripts/tt_logger_job.sh."""
        args = ["--workers", str(workers)]  # type: List[str]
        if first_run is not None:
            args += ["--first-run", str(int(first_run))]
        if last_run is not None:
            args += ["--last-run", str(int(last_run))]
        elif cfg.get("last_run", None) is not None:
            args += ["--last-run", str(int(cfg.get("last_run")))]
        if follow:
            args.append("--follow")
        if rebuild:
            args.append("--rebuild")
        if redo_changed:
            args.append("--redo-changed")
        return runner.run("silas.offline.tt_logger", *args)

    def tt_logger_status() -> str:
        """Is a time-tool logger running, and which runs are in the time-tool log."""
        from silas.offline import tt_logger

        text = tt_logger.status(cfg)
        print(text)
        return text

    def load_tt(run: int) -> Any:
        """The time-tool log of a run: one row per tag (edge, good, ...) with the correction of
        each shot from the current calibration, shift_pulse = P(edge) - P(0) and shift_ps =
        shift_pulse x ps_per_pulse (NaN for edges that are not good)."""
        df = timetool.load_edges(cfg, run)
        if not len(df):
            print("run {}: no time-tool log yet ({})".format(run, timetool.edges_path(cfg, run)))
        return df

    def set_tt_time(ps_per_pulse: float = 0.006671) -> None:
        """Time per pulse of the delay motors, for converting the timing to ps (default 0.006671:
        1 pulse = 1 um of stage, a double pass is 2 um / c)."""
        timetool.set_option(cfg, "ps_per_pulse", float(ps_per_pulse))
        print(timetool.describe(cfg))

    def show_tt() -> Dict[str, Any]:
        """The time-tool settings."""
        print(timetool.describe(cfg))
        return timetool.settings(cfg)

    # ------------------------------------------------------------ online scan viewer
    def start_scan_viewer(workers: int = 4, test: Optional[bool] = None, scan: Optional[int] = None,
                          name: Optional[str] = None) -> Optional[int]:
        """Live ROI signal per scan step (pump on, off, on/off) of the newest scan, in a new
        terminal (MPI: 1 display + `workers` readers). Needs ROIs (add_roi) and the run logger;
        at the beamline the viewer starts the logger itself if none is running.
        scan: follow a fixed scan (as scan_info) instead of the newest. test=True: simulated
        scans (default on the local profile). name: the MPCCD array (default: use_mpccd)."""
        from silas.core import runlog

        use_test = _use_test(test)
        key, settings = mpccd.get_array(cfg, name)
        if not settings.get("rois"):
            print("no ROIs for {} yet: add_roi(...) first (the viewer shows a hint until then)".format(key))
        if not use_test:
            ok, text = runlog.logger_status(cfg)
            print(text + ("" if ok else " -> the scan viewer will start one"))
        args = ["--array", key] + (["--test"] if use_test else []) + (["--scan", str(scan)] if scan is not None else [])
        launcher = runner.mpi_launcher(workers + 1) if workers > 0 else None
        if workers > 0 and launcher is None:
            print("mpi4py or mpirun not available here: one process reads and displays")
        return runner.run("silas.online.scan_view", *args, new_terminal=True, launcher=launcher,
                          execute=use_test)

    # ------------------------------------------------------------ offline scan analysis
    def analyse_scan(which: int = -1, workers: int = runner.OFFLINE_WORKERS, max_tags: Optional[int] = None,
                     show: bool = True, name: Optional[str] = None, step_reference: bool = False,
                     pairs: bool = False, tt: Optional[bool] = None, bin_ps: Optional[float] = None) -> Optional[int]:
        """Offline analysis of a logged scan (HPC, stpy): every shot of every step, ROI medians
        with the 15.87 / 84.13 % quantiles (spread of the shots), referenced shot by shot by the
        detectors of set_references, pump on / off as the ratio of the medians.
        step_reference=True: also referenced per step (median ROI / median reference);
        pairs=True: also on / off of every pump-on shot / the nearest pump-off shot. which as in scan_info
        (-1 newest, a run number, ...). workers: Python processes on this node (default 6; at
        most the cores of your bt_node; no MPI needed; 0 or 1: one). max_tags: shots per run for a quick look.
        show: open the plot when done (default; on the HPC in the browser, Ctrl-C closes it);
        show=False only saves it. name: the MPCCD array (default: use_mpccd).
        tt: time-tool binning on / off (default: set_tt_bins); bin_ps: its bin width in ps (default:
        set_tt_bins). With binning also bins.csv and plot_tt.png (shown instead of plot.png).
        Results: <output>/scans/scan_<id>/ (scan_<id>.h5, shots.csv, steps.csv, info.json, plot.png)."""
        key, settings = mpccd.get_array(cfg, name)
        if not settings.get("rois"):
            print("no ROIs for {}: add_roi(...) first".format(key))
            return None
        args = ["--scan", str(which), "--array", key]
        if max_tags:
            args += ["--max-tags", str(max_tags)]
        if show:
            args.append("--show")
        args += ["--workers", str(workers)]
        args += ["--step-reference"] if step_reference else []
        args += ["--pairs"] if pairs else []
        if tt is not None:
            args.append("--tt" if tt else "--no-tt")
        if bin_ps is not None:
            args += ["--bin-ps", str(float(bin_ps))]
        return runner.run("silas.offline.scan_analysis", *args)

    def set_tt_bins(on: Optional[bool] = None, bin_ps: Optional[float] = None, sparse_hide: Optional[bool] = None,
                    sparse_min: Optional[int] = None) -> None:
        """Time-tool binning of the scan analysis: on / off, the bin width in ps (converted to
        pulses with set_tt_time), and the sparse bins: fewer than sparse_min shots, hidden in the
        plots when sparse_hide (keys + / - and h in the plot change and store them too)."""
        from silas.core import ttbin

        for key, value in (("tt_binning", on), ("tt_bin_ps", bin_ps), ("sparse_hide", sparse_hide),
                           ("sparse_min", sparse_min)):
            if value is not None:
                ttbin.set_option(cfg, key, value)
        print(ttbin.describe(cfg))

    def scan_result(which: int = -1) -> Any:
        """The per-step table (steps.csv) of an analysed scan as a pandas DataFrame
        (which as in scan_info); prints where the plot is."""
        import pandas as pd

        sid, _ = scan.select(cfg, which)
        folder = os.path.join(cfg.output_dir(), "scans", "scan_{}".format(sid))
        path = os.path.join(folder, "steps.csv")
        if not os.path.exists(path):
            print("scan {} is not analysed yet: analyse_scan({})".format(sid, sid))
            return None
        print("plot: {}".format(os.path.join(folder, "plot.png")))
        return pd.read_csv(path)

    def load_scan(which: int = -1) -> Dict[str, Any]:
        """Everything of an analysed scan from its scan_<id>.h5 (which as in scan_info):
        {"shots", "steps", "bins", "off_steps" (pandas DataFrames; bins / off_steps only with
        time-tool binning), "info" (dict)}."""
        from silas.offline import scan_analysis

        sid, _ = scan.select(cfg, which)
        path = os.path.join(cfg.output_dir(), "scans", "scan_{}".format(sid), "scan_{}.h5".format(sid))
        if not os.path.exists(path):
            print("scan {} is not analysed yet: analyse_scan({})".format(sid, sid))
            return {}
        print(path)
        return scan_analysis.load_h5(path)

    # ------------------------------------------------------------ full-frame reduction (HPC jobs)
    def _flat(values: Any) -> List[Any]:
        out = []  # type: List[Any]
        for v in values:
            out += list(v) if isinstance(v, (list, tuple)) else [v]
        return out

    def set_ff_panels(*panels: Any, name: Optional[str] = None) -> List[int]:
        """Panels of the full-frame reduction (1-based, as in the viewer): set_ff_panels(7, 8) or
        set_ff_panels([7, 8]); set_ff_panels() for all. Stored with the MPCCD array (default: use_mpccd);
        reduce_full_frame / full_frame_job(panels=...) override it for one run."""
        from silas.offline import fullframe

        chosen = fullframe.set_panels(cfg, _flat(panels) or None, name)
        key, settings = mpccd.get_array(cfg, name)
        print("full-frame panels of {}: {}".format(
            mpccd.label(cfg, key), "all ({})".format(len(settings["panels"]))
            if len(chosen) == len(settings["panels"]) else ", ".join(str(p) for p in chosen)))
        return chosen

    def _ff_args(which: int, name: Optional[str], panels: Any, max_tags: Optional[int], step_reference: bool,
                 pairs: bool, tt: Optional[bool], bin_ps: Optional[float], min_shots: Optional[int]) -> List[str]:
        from silas.offline import fullframe

        key, settings = mpccd.get_array(cfg, name)
        args = ["--scan", str(int(which)), "--array", key]
        if panels is not None:
            sel = fullframe.parse_panels(panels, len(settings["panels"]))
            args += ["--panels", ",".join(str(k + 1) for k in sel)]
        if max_tags:
            args += ["--max-tags", str(int(max_tags))]
        args += ["--step-reference"] if step_reference else []
        args += ["--pairs"] if pairs else []
        if tt is not None:
            args.append("--tt" if tt else "--no-tt")
        if bin_ps is not None:
            args += ["--bin-ps", str(float(bin_ps))]
        if min_shots is not None:
            args += ["--min-shots", str(int(min_shots))]
        return args

    def reduce_full_frame(which: int = -1, workers: int = runner.OFFLINE_WORKERS, max_tags: Optional[int] = None,
                          name: Optional[str] = None, panels: Any = None, step_reference: bool = False,
                          pairs: bool = False, tt: Optional[bool] = None, bin_ps: Optional[float] = None,
                          min_shots: Optional[int] = None) -> Optional[int]:
        """Full-frame reduction of a logged scan IN THIS TERMINAL (HPC, stpy): the analysis of
        analyse_scan for every pixel of the chosen panels (set_ff_panels; panels=[7, 8] for this run).
        Meant for quick tests (max_tags=100) on a bt_node; whole scans: full_frame_job(...).
        which as in scan_info; workers: processes (default 6; the statistics use every core, so
        more help here); step_reference / pairs / tt / bin_ps as in analyse_scan; min_shots:
        time-tool bins with fewer shots get no images (default: set_tt_bins' sparse_min).
        Result: <output>/scans/scan_<id>/fullframe_<id>[_p<panels>].h5; load_full_frame(...)."""
        args = _ff_args(which, name, panels, max_tags, step_reference, pairs, tt, bin_ps, min_shots)
        return runner.run("silas.offline.fullframe", *(args + ["--workers", str(int(workers))]))

    def full_frame_job(which: int = -1, panels: Any = None, name: Optional[str] = None, cores: int = 16,
                       mem_gb: Optional[int] = None, hours: int = 24, step_reference: bool = False,
                       pairs: bool = False, tt: Optional[bool] = None, bin_ps: Optional[float] = None,
                       min_shots: Optional[int] = None, max_tags: Optional[int] = None,
                       submit: bool = True) -> str:
        """Full-frame reduction of a scan as a PBS batch job (scripts/fullframe_job.sh): builds the
        qsub command and submits it (submit=False, or no qsub here: only prints it, to paste on the
        login node). which: a run number of the scan (or -1 the newest; resolved now). cores
        (default 16) and mem_gb (default: estimated from the largest step in the run log and the
        panels) are the job's; other options as in reduce_full_frame. Output:
        ~/SILAS/output/fullframe_job.log. Returns the command."""
        import math
        import shlex
        import shutil
        import subprocess

        from silas.offline import fullframe

        sid, _ = scan.select(cfg, which)
        key, settings = mpccd.get_array(cfg, name)
        sel = fullframe.panel_choice(settings, panels)
        if mem_gb is None:
            log = runlog.load(runlog.runlog_path(cfg))
            part = log[log["scan_id"] == sid]
            # total_tagnumber is the run's span of tag numbers: shots = span / tag_step
            per_step = part.groupby(part["step"].fillna(-1))["total_tagnumber"].sum() if len(part) else []
            step_tags = max(1, int(settings.get("tag_step", 1) or 1))
            shots = int(max(per_step)) // step_tags if len(per_step) else 2000
            if max_tags:
                shots = min(shots, 2 * int(max_tags))        # a step is one run, rarely two (repeated)
            buffer_gb = shots * len(sel) * 512 * 1024 * 4 / 1e9   # raw panels of one step (32-bit: the worst case)
            # the buffer stays in RAM if it fits in half the job's memory (run_cache.ram_room)
            mem_gb = int(min(160, max(16, math.ceil(2.2 * buffer_gb + 0.4 * cores + 4))))
            print("memory: largest step {} shots x {} panels: buffer {:.1f} GB -> {} GB for the job".format(
                shots, len(sel), buffer_gb, mem_gb))
        env = ["SCAN={}".format(sid), "ARRAY={}".format(key), "WORKERS={}".format(int(cores))]
        if panels is not None:
            env.append("PANELS=" + ":".join(str(k + 1) for k in sel))
        if max_tags:
            env.append("MAX_TAGS={}".format(int(max_tags)))
        if step_reference:
            env.append("STEP_REFERENCE=1")
        if pairs:
            env.append("PAIRS=1")
        if tt is not None:
            env.append("TT={}".format(1 if tt else 0))
        if bin_ps is not None:
            env.append("BIN_PS={}".format(float(bin_ps)))
        if min_shots is not None:
            env.append("MIN_SHOTS={}".format(int(min_shots)))
        env.append("CONFIG={}".format(cfg.path))
        script = os.path.join(bootstrap.PACKAGE_ROOT, "scripts", "fullframe_job.sh")
        cmd = ["qsub", "-N", "bt_ff_{}".format(sid), "-l", "select=1:ncpus={}:mem={}gb".format(int(cores), int(mem_gb)),
               "-l", "walltime={}:00:00".format(int(hours)), "-v", ",".join(env), script]
        text = " ".join(shlex.quote(c) for c in cmd)
        print("panels {} of {}; job: {} cores, {} GB, {} h".format(
            ",".join(str(k + 1) for k in sel), mpccd.label(cfg, key), cores, mem_gb, hours))
        if submit and shutil.which("qsub"):
            try:
                out = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode().strip()
                print("submitted: {}   (qstat -u $USER; tail -f ~/SILAS/output/fullframe_job.log)".format(out))
                return text
            except (subprocess.CalledProcessError, OSError) as err:
                print("qsub failed here ({}); run it on the login node:".format(
                    getattr(err, "output", b"").decode(errors="replace").strip() or err))
        elif submit:
            print("no qsub here; run this on the login node (fep01):")
        print(text)
        return text

    def load_full_frame(which: Any = -1, panels: Any = None, name: Optional[str] = None) -> Any:
        """A full-frame reduction as a FullFrame (silas.offline.fullframe): which as in scan_info,
        or the path of a fullframe_*.h5 file (e.g. a copy at home). panels: which reduction of the
        scan (default: set_ff_panels). ff.image("raw", step=10, state="on"), ff.onoff("i0_1", step=10),
        ff.assemble(img), ff.steps, ff.bins, ff.info."""
        from silas.offline import fullframe

        if isinstance(which, str):
            return fullframe.FullFrame(os.path.expanduser(which))
        sid, _ = scan.select(cfg, which)
        _, settings = mpccd.get_array(cfg, name)
        sel = fullframe.panel_choice(settings, panels)
        path = fullframe.result_path(cfg, sid, sel, len(settings["panels"]))
        if not os.path.exists(path):
            folder = os.path.dirname(path)
            have = sorted(f for f in os.listdir(folder) if f.startswith("fullframe_") and f.endswith(".h5")) \
                if os.path.isdir(folder) else []
            print("no {} (full_frame_job({}) makes it){}".format(
                os.path.basename(path), sid, "; there are: " + ", ".join(have) if have else ""))
            return None
        print(path)
        return fullframe.FullFrame(path)

    def view_full_frame(which: Any = -1, panels: Any = None, name: Optional[str] = None, step: int = 1,
                        ref: Optional[str] = None, stat: str = "median", bins: bool = False) -> Optional[int]:
        """Viewer of a full-frame reduction: pump on, pump off and on / off of one step, assembled
        (silas.offline.fullframe_view). which as in load_full_frame (a scan, or the path of a
        fullframe_*.h5, e.g. at home). Keys: left / right step, up / down 10 steps, r reference
        (none -> each -> none), m median / mean, b steps / time-tool bins, l log, k colour map,
        c colour scales (percentiles or min-max of all steps), a aspect (real / fill), s save png. step (1 = first), ref
        (default: the first reference; "none": without), stat, bins: where it starts. The three
        images zoom together. HPC: opens in the browser (notebook: in the notebook)."""
        from silas.offline import fullframe

        if isinstance(which, str):
            path = os.path.expanduser(which)
        else:
            sid, _ = scan.select(cfg, which)
            _, settings = mpccd.get_array(cfg, name)
            path = fullframe.result_path(cfg, sid, fullframe.panel_choice(settings, panels), len(settings["panels"]))
        if not os.path.exists(path):
            print("no {}: load_full_frame({!r}) lists the reductions of the scan".format(path, which))
            return None
        args = [path, "--step", str(int(step)), "--stat", stat] + (["--ref", ref] if ref else []) \
            + (["--bins"] if bins else [])
        # no SACLA library needed: runs on the laptop too (execute=True also in smoke mode)
        return runner.run("silas.offline.fullframe_view", *args, new_terminal=True, execute=True)

    def set_pump(detector: Optional[str] = "optical_shutter", on_value: float = 1) -> None:
        """Point detector (alias or true name) that says whether the pump laser hits the sample,
        and its value for pump on; set_pump(None): no pump sorting (one curve)."""
        if detector is not None:
            names.resolve(cfg, detector)
        cfg.set("scan.pump_detector", detector)
        cfg.set("scan.pump_on_value", on_value)
        print("pump: {}".format("{} = {} means pump on".format(detector, on_value) if detector
                                else "none (all shots in one curve)"))

    def set_references(*detectors: Any) -> List[str]:
        """Reference point detectors (e.g. i0) of the scan analysis: set_references("i0_1"),
        set_references("i0_1", "i0_2") or a list; set_references() for none. They must be point
        detectors of the config (alias or true name). A shot without a value for a reference is
        left out of everything computed with that reference."""
        given = []  # type: List[str]
        for d in detectors:
            given += [str(x) for x in d] if isinstance(d, (list, tuple)) else [str(d)]
        point = cfg.get("point_detectors", {})
        for g in given:
            if names.resolve(cfg, g) not in point:
                raise ValueError("{!r} is not a point detector of the config: show_detectors(), "
                                 "add_point_detector(...)".format(g))
        cfg.set(scanroi.REFERENCES_KEY, given)
        print("references: {}".format(", ".join("{} ({})".format(g, names.resolve(cfg, g)) for g in given)
                                      if given else "none (the analysis is not referenced)"))
        return given

    def logger_status() -> bool:
        """Is the online run logger running? (prints why / why not)"""
        from silas.core import runlog

        ok, text = runlog.logger_status(cfg)
        print(text)
        return ok

    # ------------------------------------------------------------ energy calibration / threshold
    def set_threshold(keV: Optional[float] = None, on: bool = True, name: Optional[str] = None) -> None:
        """The lower threshold of an MPCCD array in keV of deposited energy (used by the viewer
        and all analysis): set_threshold(4.0); set_threshold(on=False) switches it off and
        keeps the value. A running viewer follows within a second."""
        if keV is not None:
            if not keV >= 0:
                raise ValueError("threshold must be >= 0 keV")
            mpccd.set_option(cfg, "corrections.threshold_keV", float(keV), name)
        mpccd.set_option(cfg, "corrections.threshold", bool(on), name)
        energy_info(name=name)

    def setup_config(beamline: Optional[int] = None, first_run: Optional[int] = None,
                     photon_energy_keV: Optional[float] = None, output: Optional[str] = None,
                     yes: bool = False) -> Optional[str]:
        """Set up this profile's config anew (silas.core.setup_config): setup_config() the rough
        default (beamline 3, no detectors); setup_config(beamline=3) the standard BL3 setup (point
        detectors, both MPCCD arrays, scan settings). Asks for the first run, the photon energy and
        the output folder (a name under /UserData/<user>/, default tmp) unless given; asks before
        replacing the config (a .bak copy is kept). Both set ev_per_absgain_adu = 3.65."""
        from silas.core import setup_config as setup

        written = setup.setup_config(beamline=beamline, first_run=first_run, photon_energy_keV=photon_energy_keV,
                                     output=output, path=cfg.path, yes=yes)
        if written:
            cfg.reload()
        return written

    def set_photon_energy(keV: Optional[float]) -> None:
        """Photon energy in keV for all analysis; None: take it from the run record (dbpy).
        When it is set, the run record is not read."""
        if keV is not None and not keV > 0:
            raise ValueError("photon energy must be > 0 keV (or None for the run record)")
        cfg.set(energy.PHOTON_ENERGY_KEY, None if keV is None else float(keV))
        print("photon energy: {}".format("{:g} keV (config)".format(keV) if keV is not None
                                         else "from the run record"))

    def set_energy_calibration(ev_per_absgain_adu: float = energy.EV_PER_ABSGAIN_ADU,
                               name: Optional[str] = None) -> None:
        """ADU calibration of an MPCCD array: keV/ADU of a panel = mp_absgain x this / 1000.
        Nominal 3.65 eV (Si); measured values are typically 3.63-3.69."""
        if not 1.0 < ev_per_absgain_adu < 10.0:
            raise ValueError("expected about 3.65 eV, got {}".format(ev_per_absgain_adu))
        mpccd.set_option(cfg, "calibration.ev_per_absgain_adu", float(ev_per_absgain_adu), name)
        energy_info(name=name)

    def set_xfel_shutter(detector: Optional[str] = "xfel_shutter") -> None:
        """Set the X-FEL shutter detector from point detectors (alias or true name) of the config (default: "xfel_shutter")."""
        cfg.set("xfel_shutter", detector)
        print("X-FEL shutter: {}".format(detector))

    def set_last_run(run: int) -> None:
        """Set the last run number of the config (cfg.set("last_run", run) once)."""
        cfg.set("last_run", int(run))
        print("last run: {}".format(run))

    # ------------------------------------------------------------ regions of interest
    def add_roi(roi_name: str, rows: Any, cols: Any, name: Optional[str] = None) -> None:
        """Add or replace a ROI: rows (first, last) and columns (first, last), inclusive, in pixels
        from the detector origin as the MPCCD viewer shows them under the mouse, e.g.
        add_roi("bragg", (316, 366), (229, 279)). At most 10. name: the MPCCD array (default:
        use_mpccd). A running viewer draws it within a second."""
        roi.add_roi(cfg, roi_name, rows, cols, name)
        show_rois(name)

    def remove_roi(roi_name: str, name: Optional[str] = None) -> None:
        """Remove one ROI (name: the MPCCD array, default: use_mpccd)."""
        roi.remove_roi(cfg, roi_name, name)
        show_rois(name)

    def clear_rois(name: Optional[str] = None) -> None:
        """Remove all ROIs of an MPCCD array (default: use_mpccd)."""
        roi.clear_rois(cfg, name)
        print("all ROIs removed")

    def show_rois(name: Optional[str] = None) -> Dict[str, Any]:
        """Print and return the ROIs of an MPCCD array (pixel counts and panels: see the viewer)."""
        key, _ = mpccd.get_array(cfg, name)
        rois = roi.get_rois(cfg, name)
        print("MPCCD array {}{}".format(mpccd.label(cfg, key), "" if name else "  (the default: use_mpccd)"))
        print(roi.describe(rois))
        others = [mpccd.label(cfg, a) for a in mpccd.array_names(cfg)
                  if a != key and (cfg.get("detectors")[a].get("rois") or {})]
        if others:
            print("other arrays with ROIs: {}  (show_rois(name=...))".format(", ".join(others)))
        return rois

    def use_mpccd(name: Optional[str] = None) -> str:
        """The MPCCD array that commands use when none is named (add_roi, set_threshold,
        analyse_scan, ...): use_mpccd("octal"). start_mpccd_viewer(name=...) also sets it.
        Without a name: print the arrays and which is the default."""
        if name:
            key = mpccd.set_default_array(cfg, name)
            print("default MPCCD array: {}".format(mpccd.label(cfg, key)))
            return key
        key = str(mpccd.default_array(cfg))
        for a in mpccd.array_names(cfg):
            n_rois = len(cfg.get("detectors")[a].get("rois") or {})
            print("{} {}  {} ROIs".format("*" if a == key else " ", mpccd.label(cfg, a), n_rois))
        print("* = default (use_mpccd(name) to change)")
        return key

    def energy_info(run: Optional[int] = None, name: Optional[str] = None) -> Optional[float]:
        """Print photon energy, ADU calibration and threshold of an MPCCD array; returns the
        photon energy (keV). run: for the photon energy from the run record (default: newest
        run, only read when the config has no photon energy)."""
        key, settings = mpccd.get_array(cfg, name)
        if run is None and energy.photon_energy_override(cfg) is None and sacla.is_live():
            try:
                run = db.newest_run(cfg.beamline)
            except Exception as err:  # noqa: BLE001  no dbpy here etc.
                print("newest run not readable: {}".format(err))
        print(key)
        print(energy.describe(cfg, settings, run))
        return energy.photon_energy(cfg, run)

    cmds = {
        # config / names
        "add_detector": _bind(cfg, names.add_detector),
        "add_point_detector": _bind(cfg, names.add_point_detector),
        "add_point_detectors": _bind(cfg, names.add_point_detectors),
        "set_alias": _bind(cfg, names.set_alias),
        "set_unit": _bind(cfg, names.set_unit),
        "remove_detector": _bind(cfg, names.remove),
        "resolve": _bind(cfg, names.resolve),
        "show_detectors": _bind(cfg, names.show),
        "add_standard_detectors": _bind(cfg, standard.add_standard_detectors),
        "setup_config": setup_config,
        "check_environment": envcheck.check,
        # moving the code
        "sync_to_hpc": sync_to_hpc,
        # MPCCD array
        "add_mpccd": _bind(cfg, mpccd.add_mpccd),
        "show_mpccd": _bind(cfg, mpccd.show),
        "set_mpccd": set_mpccd,
        "set_threshold": set_threshold,
        "set_photon_energy": set_photon_energy,
        "set_energy_calibration": set_energy_calibration,
        "set_xfel_shutter": set_xfel_shutter,
        "set_last_run": set_last_run,
        "energy_info": energy_info,
        "start_scan_viewer": start_scan_viewer,
        "set_pump": set_pump,
        "analyse_scan": analyse_scan,
        "scan_result": scan_result,
        "set_tt_bins": set_tt_bins,
        "load_scan": load_scan,
        "set_ff_panels": set_ff_panels,
        "reduce_full_frame": reduce_full_frame,
        "full_frame_job": full_frame_job,
        "load_full_frame": load_full_frame,
        "view_full_frame": view_full_frame,
        "logger_status": logger_status,
        "add_roi": add_roi,
        "remove_roi": remove_roi,
        "clear_rois": clear_rois,
        "show_rois": show_rois,
        "use_mpccd": use_mpccd,
        "set_references": set_references,
        "start_mpccd_viewer": start_mpccd_viewer,
        "capture_dark": capture_dark,
        "set_dark": set_dark,
        # time tool
        "start_tt_viewer": start_tt_viewer,
        "set_tt_roi": set_tt_roi,
        "set_tt_binning": set_tt_binning,
        "set_tt_background": set_tt_background,
        "capture_tt_background": capture_tt_background,
        "set_tt_camera": set_tt_camera,
        "set_tt_laser": set_tt_laser,
        "set_tt_edge": set_tt_edge,
        "calibrate_tt": calibrate_tt,
        "compare_tt_sacla": compare_tt_sacla,
        "start_tt_logger": start_tt_logger,
        "make_tt_log": make_tt_log,
        "tt_logger_status": tt_logger_status,
        "load_tt": load_tt,
        "set_tt_time": set_tt_time,
        "show_tt": show_tt,
        # run log
        "start_online_logger": start_online_logger,
        "make_runlog": make_runlog,
        "show_runlog": show_runlog,
        "show_scans": show_scans,
        "scan_info": scan_info,
        "scan_range": scan_range,
        "scan_run_list": scan_run_list,
        "scan_motor": scan_motor,
        "scan_runs": scan_runs,
        # dbpy
        "newest_runs": newest_runs,
        "run_summary": run_summary,
        "run_detectors": run_detectors,
        "find_equipment": find_equipment,
        "newest_tag": newest_tag,
        "read_point": read_point,
        "scan_overview": scan_overview,
    }  # type: Dict[str, Any]
    return cmds
