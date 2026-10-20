"""Online scan viewer: ROI signal per scan step, pump on / off and their ratio, live.

    start_scan_viewer()                                  # from the session (new terminal, MPI)
    mpirun -n 5 python -m silas.online.scan_view      # 1 display + 4 readers
    python -m silas.online.scan_view --test           # simulated scans, one process, no SACLA

What it does
  * follows the NEWEST scan of the run log (the online run logger must run; at the
    beamline the viewer starts one if none is running). When a new scan starts, it clears
    everything and follows that one. --scan N follows a fixed scan instead (as scan_info).
  * every run of the scan is one step. For every shot: X-rays off / bad beam are dropped,
    the pump state is read (config scan.pump_detector, value scan.pump_on_value = pump on;
    no pump detector: one curve), only the panels the ROIs cover are read, and the ROI pixels
    are corrected (dark, keV threshold) and summed in keV.
  * per step, pump state and ROI: sum, sum of squares and number of shots. Shown:
    mean keV per pixel per shot (top: pump on solid, pump off dashed) and on / off (bottom),
    with Poisson error bars (sqrt(photons)).

Keeping up (MPI): reader k of N takes the shots with (tag / tag_step) mod N == k. Each reader
works newest-first: new shots of the running step first, then it goes back through older
shots and older steps it has not read yet, as long as the online buffer still has them.
A shot no longer in the buffer is counted as lost (and everything older with it). The
status line shows read / lost / dropped shots and the backlog still to read.

Keys: 1..9, 0 show / hide ROI 1..10;  e error bars on / off;  r re-read (clear and start
the scan again);  q quit.
"""
import argparse
import json
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import roi, sacla, scanroi
from silas.core.corrections import Corrections
from silas.online import scan_feed
from silas.online.mpccd_view import Settings, _pyplot, corrections_id, kev_per_adu

TAG_DATA = 21
TAG_CTRL = 22
BATCH = 16                   # shots per flag read (dbpy) and per step() call
RETRY_S = 3.0                # pump state unknown for a shot this new: try again later
TAGS_PER_S = 60              # tag numbers advance 60 per second (also at 30 Hz, tag step 2)


def analysis_key(settings: Settings, infos: List[Dict[str, Any]]) -> str:
    """Everything that changes the ROI sums: corrections, calibration, geometry, ROIs."""
    s = settings.s
    return json.dumps([corrections_id(settings, infos), s.get("geometry"), s.get("rois") or {}], sort_keys=True)


def roi_setup(det_ids: List[str], infos: List[Dict[str, Any]],
              settings: Settings) -> Tuple[Optional[roi.RoiMap], np.ndarray, Corrections]:
    """(ROI pixels or None, keV per ADU of every panel, corrections) for the current settings."""
    return roi.analysis_setup(det_ids, infos, settings.s)


# ====================================================================== reader
class ScanWorker(object):
    """Reads and sums the shots of one reader (wid of nw) for the scan it is given."""

    def __init__(self, feed: Any, settings: Settings, wid: int = 0, nw: int = 1) -> None:
        self.feed = feed
        self.settings = settings
        self.wid, self.nw = wid, nw
        self.tag_step = int(settings.s.get("tag_step", 1))
        self.infos = feed.infos()
        self.key = ""
        self.scan = None  # type: Optional[Dict[str, Any]]
        self.runs = {}  # type: Dict[int, Dict[str, Any]]
        # runs, newest first
        self.order = []  # type: List[int]
        # own tags still to read, newest first
        self.queue = {}  # type: Dict[int, Deque[int]]
        # highest tag put in the queue
        self.seen = {}  # type: Dict[int, int]
        # pump state not yet in the SyncDB
        self.retry = {}  # type: Dict[int, Deque[int]]
        self.sums = scanroi.ScanSums(0, feed.n_states)
        self.rmap = None  # type: Optional[roi.RoiMap]
        self.corr = None  # type: Optional[Corrections]
        self.kev = np.zeros(0)
        self.setup()

    # ------------------------------------------------------------ setup
    def setup(self) -> bool:
        """(Re)build ROI pixels and corrections if the settings changed. True if they did."""
        key = analysis_key(self.settings, self.infos)
        if key == self.key:
            return False
        self.key = key
        self.rmap, self.kev, self.corr = roi_setup(self.feed.det_ids, self.infos, self.settings)
        for m in self.corr.messages:
            print(m)
        self.restart()
        return True

    def restart(self) -> None:
        """Forget everything read; the scan's shots are read again (as far as still available)."""
        n = len(self.rmap.names) if self.rmap else 0
        self.sums = scanroi.ScanSums(n, self.feed.n_states)
        self.queue, self.seen, self.retry = {}, {}, {}

    def set_scan(self, scan: Optional[Dict[str, Any]]) -> None:
        """The scan to follow (from rank 0). A different scan id starts from zero."""
        if scan is None:
            return
        if self.scan is None or scan["scan_id"] != self.scan["scan_id"]:
            self.restart()
        self.scan = scan
        self.runs = {int(r["run"]): r for r in scan["runs"]}
        self.order = sorted(self.runs, reverse=True)

    # ------------------------------------------------------------ reading
    def _mine(self, tag: int) -> bool:
        return (tag // self.tag_step) % self.nw == self.wid

    def _fill(self, newest: int) -> None:
        """Put new own tags of every run in its queue (newest first)."""
        for run in self.order:
            info = self.runs[run]
            if info.get("end_tag") is not None and self.seen.get(run, -1) >= int(info["end_tag"]):
                continue
            new = [t for t in self.feed.tags(info, newest) if t > self.seen.get(run, -1) and self._mine(t)]
            if new:
                q = self.queue.setdefault(run, deque())
                q.extendleft(new)                       # ascending list -> newest ends up first
                self.seen[run] = new[-1]

    def _lost_from(self, run: int) -> None:
        """A shot of ``run`` is gone from the buffer: so are its older ones and older runs."""
        for r in self.order:
            if r <= run:
                n = len(self.queue.get(r, ())) + len(self.retry.get(r, ()))
                if n:
                    self.sums.count(r, "lost", n)
                self.queue[r] = deque()
                self.retry[r] = deque()

    def backlog(self) -> int:
        return sum(len(q) for q in self.queue.values()) + sum(len(q) for q in self.retry.values())

    def step(self) -> bool:
        """Read up to BATCH shots, newest first. False if there was nothing to do."""
        if self.scan is None or self.rmap is None or not self.rmap.panels:
            return False
        newest = self.feed.newest_tag()
        self._fill(newest)
        run = next((r for r in self.order if self.queue.get(r)), None)
        if run is None:
            run = next((r for r in self.order if self.retry.get(r)), None)
            if run is None:
                return False
            q = self.retry[run]
        else:
            q = self.queue[run]
        tags = [q.popleft() for _ in range(min(BATCH, len(q)))]
        info = self.runs[run]
        use, state = self.feed.flags(info, tags)
        thr = self.corr.threshold_adu if self.corr is not None and self.corr.settings.get("threshold") else None
        dark = self.corr.dark if self.corr is not None else None
        for i, tag in enumerate(tags):
            if not use[i]:
                self.sums.count(run, "xray_off")
                continue
            if state[i] < 0:
                if newest - tag < RETRY_S * TAGS_PER_S:
                    self.retry.setdefault(run, deque()).append(tag)   # SyncDB not there yet
                else:
                    self.sums.count(run, "no_pump")
                continue
            data = self.feed.read(tag, self.rmap.panels)
            if data is scan_feed.GONE:
                self.sums.count(run, "lost", 1 + sum(1 for t in tags[i + 1:]))
                self._lost_from(run)
                return True
            if data is None:
                self.sums.count(run, "lost")
                continue
            self.sums.add_shot(run, int(state[i]), self.rmap.sums(data, self.kev, dark, thr))
        return True

    def take(self) -> Dict[str, Any]:
        """Message for rank 0: the sums since the last take, and the backlog."""
        return {"key": self.key, "scan_id": self.scan["scan_id"] if self.scan else None,
                "sums": self.sums.take(), "backlog": self.backlog()}


# ====================================================================== display
COLORS = ["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]


class ScanDisplay(object):
    def __init__(self, settings: Settings, feed: Any, label: str, which: Optional[int], live: bool) -> None:
        plt = _pyplot(settings.s["display"].get("backend"))
        import matplotlib

        for k in list(matplotlib.rcParams):
            if k.startswith("keymap."):
                matplotlib.rcParams[k] = []
        self.plt = plt
        self.settings = settings
        self.feed = feed
        self.infos = feed.infos()
        self.label = label
        self.which = which
        self.live = live
        self.key = analysis_key(settings, self.infos)
        self.scan = None  # type: Optional[Dict[str, Any]]
        self.total = scanroi.ScanSums(0, feed.n_states)
        self.backlog = {}  # type: Dict[int, int]
        self.hidden = set()  # type: set
        self.errors = True
        self.running = True
        self.restart_requested = False
        self.logger_text = ""
        self.received = []  # type: List[Tuple[float, int]]
        self.rmap_names = []  # type: List[str]
        self.n_pixels = []  # type: List[int]
        self._roi_info()
        from silas.core import plotting

        plotting.ion(plt)                                  # before the figure: the window updates by itself
        self.fig, (self.ax, self.rax) = plt.subplots(2, 1, figsize=(11, 9), sharex=True,
                                                     gridspec_kw={"height_ratios": [3, 2]})
        manager = self.fig.canvas.manager
        if manager is not None:
            try:
                manager.set_window_title("scan viewer - " + settings.key)
            except AttributeError:
                pass
        self.fig.subplots_adjust(top=0.84, hspace=0.08, right=0.8)
        self.status = self.fig.text(0.01, 0.99, "", va="top", family="monospace", fontsize=8.5)
        self.fig.canvas.mpl_connect("key_press_event", self.on_key)
        self.fig.canvas.mpl_connect("close_event", lambda e: self.stop())

    def _roi_info(self) -> None:
        rmap, _, _ = roi_setup(self.feed.det_ids, self.infos, self.settings)
        self.rmap_names = list(rmap.names) if rmap else []
        self.n_pixels = list(rmap.n_pixels) if rmap else []
        self.total = scanroi.ScanSums(len(self.rmap_names), self.feed.n_states)

    # ------------------------------------------------------------ data
    def settings_changed(self) -> bool:
        key = analysis_key(self.settings, self.infos)
        if key == self.key:
            return False
        self.key = key
        self._roi_info()
        self.backlog = {}
        return True

    def set_scan(self, scan: Optional[Dict[str, Any]]) -> bool:
        """True if this is a different scan (everything starts again)."""
        new = scan is not None and (self.scan is None or scan["scan_id"] != self.scan["scan_id"])
        if new:
            self.total = scanroi.ScanSums(len(self.rmap_names), self.feed.n_states)
            self.backlog = {}
        if scan is not None:
            self.scan = scan
        return new

    def restart(self) -> None:
        self.total = scanroi.ScanSums(len(self.rmap_names), self.feed.n_states)
        self.backlog = {}

    def add(self, msg: Dict[str, Any], source: int = 0) -> None:
        if msg.get("key") != self.key or self.scan is None or msg.get("scan_id") != self.scan["scan_id"]:
            return
        self.total.merge(msg["sums"])
        self.backlog[source] = int(msg.get("backlog", 0))
        n = sum(r["done"] for r in msg["sums"].runs.values())
        self.received.append((time.time(), n))

    def rate(self) -> float:
        now = time.time()
        self.received = [(t, n) for t, n in self.received if now - t < 10.0]
        if not self.received:
            return 0.0
        return sum(n for _, n in self.received) / max(now - self.received[0][0], 1.0)

    # ------------------------------------------------------------ drawing
    def progress(self) -> str:
        """One line for the terminal."""
        t = self.total.totals()
        scan = self.scan
        return "{}  scan {}  steps {}  read {}  lost {}  dropped {}  backlog {}  {:.1f} shots/s".format(
            time.strftime("%H:%M:%S"), scan["scan_id"] if scan else "-", len(scan["runs"]) if scan else 0,
            t["done"], t["lost"], t["xray_off"] + t["no_pump"], sum(self.backlog.values()), self.rate())

    def draw(self) -> None:
        ax, rax = self.ax, self.rax
        ax.cla()
        rax.cla()
        scan = self.scan
        e_ph = None
        if scan and scan["runs"]:
            e_ph = self.feed.photon_energy(int(scan["runs"][-1]["run"]))
        empty = [n for n, p in zip(self.rmap_names, self.n_pixels) if p == 0]
        if not self.rmap_names:
            ax.text(0.5, 0.5, "no ROIs: add_roi(...) in the session", ha="center", transform=ax.transAxes)
        elif len(empty) == len(self.rmap_names):
            ax.text(0.5, 0.5, "no ROI covers any detector pixel: {}\nnothing is read. ROIs are in pixels FROM THE "
                    "DETECTOR ORIGIN\n(the MPCCD viewer's mouse read-out); check them with show_rois() and the "
                    "MPCCD viewer".format(", ".join(empty)), ha="center", va="center", color="red",
                    transform=ax.transAxes)
        elif scan is None:
            ax.text(0.5, 0.5, "no scan yet (run logger: {})".format(self.logger_text or "-"),
                    ha="center", transform=ax.transAxes)
        else:
            table = scanroi.step_table(self.total, scan["runs"])
            m = scanroi.means(table, self.n_pixels, e_ph)
            x = m["x"]
            two = self.feed.n_states == 2
            for i, name in enumerate(self.rmap_names):
                if i in self.hidden or not len(x):
                    continue
                c = COLORS[i % len(COLORS)]
                label = "{} {}".format(i + 1 if i < 9 else 0, name)
                err_on = m["err"][0, :, i] if self.errors else None
                ax.errorbar(x, m["mean"][0, :, i], yerr=err_on, color=c, marker="o", ms=4, lw=1.2,
                            capsize=2, label=label + (" on" if two else ""))
                if two:
                    err_off = m["err"][1, :, i] if self.errors else None
                    ax.errorbar(x, m["mean"][1, :, i], yerr=err_off, color=c, marker="o", ms=4, mfc="white",
                                ls="--", lw=1.0, capsize=2, label=label + " off")
                    r, re = scanroi.ratio(m["mean"][:, :, i:i + 1], m["err"][:, :, i:i + 1])
                    rax.errorbar(x, r[:, 0], yerr=re[:, 0] if self.errors else None, color=c, marker="o",
                                 ms=4, lw=1.2, capsize=2, label=label)
            if not two:
                rax.text(0.5, 0.5, "no pump detector set: no on / off\n(set_pump(\"optical_shutter\", 1))",
                         ha="center", va="center", transform=rax.transAxes)
            else:
                rax.axhline(1.0, color="0.6", lw=0.8)
            if table and table[0]["x_is_value"]:
                rax.set_xlabel(scan.get("motor") or "scanned motor")
            else:
                rax.set_xlabel("scan step (motor value not known yet)")
            if any(i not in self.hidden for i in range(len(self.rmap_names))):
                ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1.0))
        ax.set_ylabel("keV per pixel per shot")
        rax.set_ylabel("pump on / off")
        ax.grid(alpha=0.3)
        rax.grid(alpha=0.3)
        self._status(e_ph)
        self.fig.canvas.draw_idle()

    def _status(self, e_ph: Optional[float]) -> None:
        scan = self.scan
        t = self.total.totals()
        c = self.settings.s["corrections"]
        lines = []
        if scan:
            done = sum(1 for r in scan["runs"] if r.get("end_tag") is not None)
            lines.append("scan {}  [{}]  {}   motor {}   steps {} ({} finished)   {}".format(
                scan["scan_id"], self.settings.key, scan.get("status", ""), scan.get("motor") or "?",
                len(scan["runs"]), done, "following the newest scan" if self.which is None
                else "fixed scan ({})".format(self.which)))
        else:
            lines.append("no scan yet   [{}]".format(self.settings.key))
        lines.append("shots read {}   lost {}   dropped: X-rays off / bad beam {}, pump unknown {}   "
                     "backlog {}   {:.1f} shots/s".format(t["done"], t["lost"], t["xray_off"], t["no_pump"],
                                                         sum(self.backlog.values()), self.rate()))
        lines.append("source: {}   photon {}   threshold {}   dark {}   errors {}".format(
            self.label, "{:g} keV".format(e_ph) if e_ph else "unknown",
            "{:g} keV".format(float(c.get("threshold_keV", 0))) if c.get("threshold") else "off",
            "on" if c.get("dark") and c.get("dark_file") else "OFF", "on" if self.errors else "off"))
        lines.append("run logger: {}".format(self.logger_text or "-"))
        empty = [n for n, p in zip(self.rmap_names, self.n_pixels) if p == 0]
        if empty:
            lines.append("WARNING: ROI(s) {} cover no detector pixel (outside the detector or in a gap)".format(
                ", ".join(empty)))
        lines.append("keys: 1..9,0 ROIs   e errors   r re-read   q quit")
        self.status.set_text("\n".join(lines))

    # ------------------------------------------------------------ keys
    def on_key(self, event: Any) -> None:
        k = event.key or ""
        if len(k) == 1 and k.isdigit():
            i = (int(k) - 1) % 10
            if i in self.hidden:
                self.hidden.discard(i)
            else:
                self.hidden.add(i)
        elif k == "e":
            self.errors = not self.errors
        elif k == "r":
            self.restart_requested = True
        elif k == "q":
            self.stop()
        else:
            return
        self.draw()

    def stop(self) -> None:
        self.running = False


# ====================================================================== main loops
def _check_logger(settings: Settings, start: bool) -> str:
    """Status text of the run logger; starts one (new terminal) if none runs and start=True."""
    from silas.core import runlog, runner

    ok, text = runlog.logger_status(settings.cfg)
    if not ok and start:
        print(text + ": starting the online run logger in a new terminal")
        try:
            runner.run("silas.online.logger", new_terminal=True)
            text = "started by the scan viewer (check its terminal)"
        except Exception as err:  # noqa: BLE001
            text = "NOT running and could not be started: {}".format(err)
    return text


def run_viewer(args: Any) -> None:
    from silas.core import mpi

    comm, rank, size, MPI = mpi.world()  # MPI only under mpirun (see core/mpi.py)

    settings = Settings(args.array)
    tag_step = int(settings.s.get("tag_step", 1))
    feed = scan_feed.open_feed(settings.cfg, list(settings.s["panels"]), args.test, tag_step)

    if rank > 0:  # ------------------------------------------- reader
        worker = ScanWorker(feed, settings, wid=rank - 1, nw=size - 1)
        next_report = time.time() + 0.5
        while True:
            quit_now = False
            while comm.Iprobe(source=0, tag=TAG_CTRL):
                msg = comm.recv(source=0, tag=TAG_CTRL)
                if msg is None:
                    quit_now = True
                    break
                if msg.get("restart"):
                    worker.restart()
                if "scan" in msg:
                    worker.set_scan(msg["scan"])
            if quit_now:
                break
            if settings.refresh():
                worker.setup()
            busy = worker.step()
            if time.time() >= next_report:
                comm.send(worker.take(), dest=0, tag=TAG_DATA)
                next_report = time.time() + 0.5
            if not busy:
                time.sleep(0.005)
        return

    # ----------------------------------------------------------- display (rank 0)
    label = "TEST scans" if args.test else "olpy (live)"
    if size > 1:
        label += ", {} MPI readers".format(size - 1)
    display = ScanDisplay(settings, feed, label, args.scan, live=not args.test)
    if args.test:
        display.logger_text = "not needed (test mode: simulated scans)"
    local = ScanWorker(feed, settings) if size == 1 else None
    print("scan viewer: {}. Keys: 1..9,0 ROIs, e errors, r re-read, q quit.".format(label))
    for name, npx in zip(display.rmap_names, display.n_pixels):
        print("  ROI {:<16s} {:>8d} pixels{}".format(name, npx, "   <- covers NO detector pixel" if npx == 0 else ""))
    from silas.core import plotting

    st = {"next_scan": 0.0, "next_draw": 0.0, "next_logger": 0.0, "next_print": 0.0}

    def tick() -> bool:
        """One round: settings, logger, scan, restart, read / collect, draw and print when due."""
        now = time.time()
        if settings.refresh() and display.settings_changed():
            if local is not None:
                local.setup()
        if not args.test and now >= st["next_logger"]:
            display.logger_text = _check_logger(settings, start=st["next_logger"] == 0.0)
            st["next_logger"] = now + 10.0
        if now >= st["next_scan"]:
            scan = feed.scan(args.scan)
            new = display.set_scan(scan)
            if new:
                print("following scan {}".format(scan["scan_id"] if scan else None))
            if local is not None:
                local.set_scan(scan)
            elif comm is not None:
                for r in range(1, size):
                    comm.send({"scan": scan}, dest=r, tag=TAG_CTRL)
            st["next_scan"] = now + (1.0 if args.test else 2.0)
        if display.restart_requested:
            display.restart_requested = False
            display.restart()
            if local is not None:
                local.restart()
            elif comm is not None:
                for r in range(1, size):
                    comm.send({"restart": True}, dest=r, tag=TAG_CTRL)
        if local is not None:
            t_end = time.time() + 0.3
            while time.time() < t_end:
                if not local.step():
                    time.sleep(0.005)
            display.add(local.take())
        else:
            status = MPI.Status()
            while comm.Iprobe(tag=TAG_DATA, status=status):
                src = status.Get_source()
                display.add(comm.recv(source=src, tag=TAG_DATA), src)
        if time.time() >= st["next_draw"]:
            display.draw()
            display.fig.canvas.flush_events()
            st["next_draw"] = time.time() + 1.0
        if time.time() >= st["next_print"]:
            print(display.progress())
            st["next_print"] = time.time() + 10.0
        return bool(display.running)

    def stop() -> None:
        if comm is not None and size > 1:
            for r in range(1, size):
                comm.send(None, dest=r, tag=TAG_CTRL)
        plotting.close_all(display.plt, [display.fig])
        print("scan viewer stopped")

    detached = False      # True: running on in a notebook, stop() is called when it ends
    try:
        detached = plotting.run_loop(display.plt, tick, 0.05, on_stop=stop)   # a window, the browser or a notebook
    except KeyboardInterrupt:
        pass
    finally:
        if not detached:
            stop()


@sacla.requires_sacla
def run_live(args: Any) -> None:
    run_viewer(args)


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="online scan viewer (ROIs per scan step, pump on/off)")
    ap.add_argument("--array", help="MPCCD array name or alias (default: the first)")
    ap.add_argument("--scan", type=int, help="follow this scan (-1 newest = default, run number, ...)")
    ap.add_argument("--test", action="store_true", help="simulated scans instead of the beamline")
    args = ap.parse_args(argv)
    if args.test:
        run_viewer(args)
    else:
        run_live(args)


if __name__ == "__main__":
    main()
