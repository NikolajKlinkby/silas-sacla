"""Time-tool viewer: single shots of the timing-monitor camera, its ROI and the ROI's trace.

    start_tt_viewer()                                      # from the session (new terminal)
    mpirun -n 5 python -m silas.online.tt_view          # live: 1 display + 4 reader processes
    python -m silas.online.tt_view --test               # test camera, one process, no SACLA
    python -m silas.online.tt_view --run 1234501        # a STORED run (HPC): step through its shots

The window shows the camera image of ONE shot with the ROI, the ROI's trace (mean over rows
or columns, silas.core.timetool.trace) with the background's trace, and the trace
divided by (or minus) the background's trace when a background is set, with its smoothed
version, the fitted step and the EDGE (silas.core.timetool.find_edge, camera pixels) as
vertical lines in both trace plots: solid = the edge (green good, red not), dotted = the
derivative's edge.

Live: with MPI, ranks 1..N read the camera: reader k takes the shots with
(tag / tag_step) mod N == k and makes the trace and finds the edge of every one of its
shots, and sends them to rank 0 every "update_s" seconds. Rank 0 keeps the last traces, reads the image of the shot it shows itself, and
shows the readers' speed. Space pauses; the arrows then step through the kept shots.

Offline (--run N): the run's shots one by one (arrows, PgUp / PgDn, Home / End, g + tag or
shot number + Enter); f shows all shots, only timing shots (X-rays + laser on, beam OK) or
only background shots (laser on, X-rays off).

The settings are the config's "timetool" section (set_tt_roi, set_tt_binning, ...); the file
is watched, so changes from the session or from keys reach the window within a second.
Press h in the window for the keys.
"""
import argparse
import bisect
import datetime
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import sacla, timetool
from silas.core.config import Config
from silas.online.tt_source import (UNKNOWN, classify_one, detect_tag_step, open_camera,
                                       shot_flags_text)

TAG_DATA = 21
TAG_STOP = 22
RING = 900            # traces kept by the live display (30 s at 30 Hz)
CMAPS = ["viridis", "magma", "inferno", "cividis", "gray", "gray_r", "jet", "coolwarm"]
FILTERS = [("all shots", ()), ("timing shots", (timetool.TIMING,)), ("background shots", (timetool.BACKGROUND,))]

HELP = """keys
 h        this help on / off
 space    pause / go on        (live)
 <- ->    previous / next shot
 PgUp/Dn  10 shots back / on
 Home/End first / last shot
 g        go to: type a tag or a
          shot number, Enter
 f        all / timing /
          background shots

ROI and trace
 e        draw the ROI with the
          mouse (drag a box)
 x        ROI = whole image
 b        binning rows / cols
 m        background: divide /
          subtract
 d        edge: any / rising /
          falling
 k        edge fit on / off

display
 z        reset zoom (wheel: zoom)
 l        log / linear colours
 c  C     next / previous colours
 v        freeze / auto limits
 s        save snapshot
          (png, npz, csv)
 q        quit

classes of shots
 timing      X-rays on, laser on,
             beam OK
 background  laser on, X-rays off"""


# ====================================================================== settings
class Settings(object):
    """The time-tool settings, re-read whenever the config file changes."""

    def __init__(self) -> None:
        self.cfg = Config()
        self.stamp = None  # type: Any
        self.s = {}  # type: Dict[str, Any]
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> bool:
        stamp = self.cfg.file_stamp()
        if stamp is None or (not force and stamp == self.stamp):
            return False
        self.stamp = stamp
        try:
            self.cfg.reload()
            self.s = timetool.settings(self.cfg)
        except (ValueError, KeyError) as err:
            print("config not reloaded:", err)
            return False
        return True

    def set(self, key: str, value: Any) -> None:
        timetool.set_option(self.cfg, key, value)
        self.refresh(force=True)

    def trace_key(self) -> str:
        """Everything that changes a trace."""
        return json.dumps([self.s.get("roi"), self.s.get("binning")])

    def edge_key(self) -> str:
        """Everything that changes an edge."""
        return json.dumps([self.s.get("roi"), self.s.get("binning"), self.s.get("background"), self.s.get("edge")])


# ====================================================================== live readers
class TraceReader(object):
    """Live: reads this worker's shots and makes the trace of each one."""

    def __init__(self, source: Any, settings: Settings, wid: int = 0, nworkers: int = 1, tag_step: int = 1) -> None:
        self.src = source
        self.settings = settings
        self.wid = wid
        self.nw = nworkers
        self.step_ = max(1, int(tag_step))
        self.last_tag = None  # type: Optional[int]
        self.missed = 0
        self.background = timetool.Background()
        self.reset()

    def reset(self) -> None:
        self.traces = []  # type: List[Tuple[int, str, np.ndarray, float, bool]]
        self.n = 0
        self.t_read = 0.0
        self.t_trace = 0.0
        self.t_edge = 0.0

    def step(self) -> bool:
        """Process the newest shot of this worker if there is a new one."""
        step = self.step_
        newest = self.src.newest_tag()
        slot = (newest // step) % self.nw
        target = newest - ((slot - self.wid) % self.nw) * step
        if self.last_tag is not None and target <= self.last_tag:
            return False
        if self.last_tag is not None:
            self.missed += max(0, (target - self.last_tag) // (step * self.nw) - 1)
        self.last_tag = target
        t0 = time.perf_counter()
        img = self.src.read(target)
        t1 = time.perf_counter()
        if img is None:
            self.missed += 1
            return True
        s = self.settings.s
        roi, binning = s.get("roi"), s.get("binning", "rows")
        tr = timetool.trace(img, roi, binning)
        t2 = time.perf_counter()
        self.background.load(s["background"].get("file"))
        bt = self.background.trace(roi, binning, img.shape)
        corr = timetool.correct(tr, bt, s["background"].get("mode", "divide"))
        e = timetool.find_edge(corr if corr is not None else tr, timetool.trace_axis(roi, img.shape, binning), s["edge"])
        t3 = time.perf_counter()
        self.traces.append((target, self.settings.trace_key(), tr.astype(np.float32), float(e["edge"]),
                            bool(e["good"]) and corr is not None))
        self.n += 1
        self.t_read += t1 - t0
        self.t_trace += t2 - t1
        self.t_edge += t3 - t2
        return True

    def take(self) -> Dict[str, Any]:
        msg = {"traces": self.traces, "n": self.n, "missed": self.missed, "t_read": self.t_read,
               "t_trace": self.t_trace, "t_edge": self.t_edge, "run": getattr(self.src, "run", None),
               "edge_key": self.settings.edge_key()}
        self.reset()
        self.missed = 0
        return msg


# ====================================================================== display
class TTViewer(object):
    def __init__(self, settings: Settings, src: Any, out_dir: str, live: bool, n_readers: int = 0) -> None:
        import matplotlib

        from silas.core import plotting

        plt = plotting.pyplot(settings.s["display"].get("backend"), interactive=True)
        for k in list(matplotlib.rcParams):  # our keys, not matplotlib's shortcuts
            if k.startswith("keymap."):
                matplotlib.rcParams[k] = []
        self.plt = plt
        self.settings = settings
        self.src = src
        self.out_dir = out_dir
        self.live = live
        self.n_readers = n_readers
        self.running = True
        self.paused = False
        self.show_help = True
        self.message = ""
        self.goto = None  # type: Optional[str]
        self.filter = 0
        # live: traces from the readers {tag: (trace key, trace)}, sorted tags
        self.ring = {}  # type: Dict[int, Tuple[str, np.ndarray]]
        self.edges = {}  # type: Dict[int, Tuple[float, bool]]
        self.ring_tags = []  # type: List[int]
        self.received = []  # type: List[Tuple[float, int]]
        self.missed = 0
        self.t_read = 0.0
        self.t_trace = 0.0
        self.t_edge = 0.0
        self.n_timed = 0
        self.run = getattr(src, "run", None)
        # offline: position in the (filtered) list of the run's shots
        self.order = list(range(len(src.tags))) if src.tags else []  # type: List[int]
        self.pos = 0
        self.cursor = None  # type: Optional[int]
        # what is shown
        self.tag = None  # type: Optional[int]
        self.image = None  # type: Optional[np.ndarray]
        self.image_tag = None  # type: Optional[int]
        self.trace = None  # type: Optional[np.ndarray]
        self.axis = None  # type: Optional[np.ndarray]
        self.bg_trace = None  # type: Optional[np.ndarray]
        self.corrected = None  # type: Optional[np.ndarray]
        self.edge = {}  # type: Dict[str, Any]
        self.flags = UNKNOWN
        self.lims = (0.0, 1.0)
        self.background = timetool.Background()
        self.editing = False

        plotting.ion(plt)
        self.fig = plt.figure(figsize=(15, 10.5))
        manager = self.fig.canvas.manager
        if manager is not None:
            try:
                manager.set_window_title("time-tool viewer - " + src.det_id)
            except AttributeError:
                pass
        gs = matplotlib.gridspec.GridSpec(3, 2, figure=self.fig, width_ratios=[5, 1.3],
                                          height_ratios=[3.0, 1.5, 1.5], wspace=0.12, hspace=0.32,
                                          top=0.9, bottom=0.06, left=0.06, right=0.98)
        self.ax = self.fig.add_subplot(gs[0, 0])
        self.tax = self.fig.add_subplot(gs[1, 0])
        self.cax = self.fig.add_subplot(gs[2, 0], sharex=self.tax)
        self.hax = self.fig.add_subplot(gs[:, 1])
        self.hax.axis("off")
        self.help_text = self.hax.text(0.0, 1.0, HELP, va="top", family="monospace", fontsize=8.5,
                                       transform=self.hax.transAxes)
        self.status = self.fig.text(0.01, 0.995, "", va="top", family="monospace", fontsize=9)
        self.im = None  # type: Any
        self.norm_kind = ""
        from matplotlib.patches import Rectangle

        self.roi_patch = Rectangle((0, 0), 1, 1, fill=False, ec="orange", lw=1.5)
        self.ax.add_patch(self.roi_patch)
        (self.l_raw,) = self.tax.plot([], [], "-", color="C0", lw=1.0, label="this shot")
        (self.l_bg,) = self.tax.plot([], [], "--", color="0.45", lw=1.0, label="background")
        (self.l_corr,) = self.cax.plot([], [], "-", color="C3", lw=0.8, alpha=0.7, label="this shot")
        (self.l_smooth,) = self.cax.plot([], [], "-", color="k", lw=1.0, label="smoothed")
        (self.l_fit,) = self.cax.plot([], [], "--", color="C1", lw=1.6, label="step fit")
        self.v_raw = self.tax.axvline(0.0, color="C2", lw=1.4)
        self.v_corr = self.cax.axvline(0.0, color="C2", lw=1.4)
        self.v_der_raw = self.tax.axvline(0.0, color="C2", lw=1.0, ls=":")
        self.v_der = self.cax.axvline(0.0, color="C2", lw=1.0, ls=":")
        for v in (self.v_raw, self.v_corr, self.v_der_raw, self.v_der):
            v.set_visible(False)
        self.edge_text = self.cax.text(0.005, 0.03, "", transform=self.cax.transAxes, fontsize=8.5,
                                       family="monospace", va="bottom")
        self.ref_line = self.cax.axhline(1.0, color="0.6", lw=0.8, ls=":")
        self.no_bg = self.cax.text(0.5, 0.5, "", ha="center", va="center", transform=self.cax.transAxes,
                                   color="0.4", fontsize=10)
        self.tax.set_ylabel("ROI mean (counts)")
        self.tax.legend(loc="upper right", fontsize=8)
        from matplotlib.widgets import RectangleSelector

        self.selector = RectangleSelector(self.ax, self._roi_drawn, useblit=False, minspanx=3,
                                          minspany=3, spancoords="pixels", interactive=False)
        self.selector.set_active(False)
        self.fig.canvas.mpl_connect("key_press_event", self.on_key)
        self.fig.canvas.mpl_connect("scroll_event", self.on_scroll)
        self.fig.canvas.mpl_connect("close_event", lambda e: self.stop())

    # ------------------------------------------------------------ live data
    def add(self, msg: Dict[str, Any]) -> None:
        """Traces from a reader."""
        key = self.settings.trace_key()
        same_edges = msg.get("edge_key") == self.settings.edge_key()
        for tag, k, tr, edge, good in msg.get("traces", []):
            if k != key or tag in self.ring:
                continue
            self.ring[tag] = (k, tr)
            if same_edges:
                self.edges[tag] = (edge, good)
            bisect.insort(self.ring_tags, tag)
        while len(self.ring_tags) > RING:
            old = self.ring_tags.pop(0)
            self.ring.pop(old, None)
            self.edges.pop(old, None)
        if msg.get("n"):
            self.received.append((time.time(), int(msg["n"])))
            self.t_read += float(msg.get("t_read", 0.0))
            self.t_trace += float(msg.get("t_trace", 0.0))
            self.t_edge += float(msg.get("t_edge", 0.0))
            self.n_timed += int(msg["n"])
        self.missed += int(msg.get("missed", 0))
        if msg.get("run") is not None:
            self.run = int(msg["run"])

    def settings_changed(self) -> None:
        """New ROI / binning: the kept traces no longer fit."""
        key = self.settings.trace_key()
        drop = [t for t, (k, _) in self.ring.items() if k != key]
        for t in drop:
            self.ring.pop(t, None)
        if drop:
            self.ring_tags = sorted(self.ring)
        self.edges = {}             # found with other settings

    def rate(self) -> float:
        now = time.time()
        self.received = [(t, n) for t, n in self.received if now - t < 10.0]
        if len(self.received) < 2:      # each report holds the shots since the one before
            return 0.0
        span = max(self.received[-1][0] - self.received[0][0], 1e-3)
        return sum(n for _, n in self.received[1:]) / span

    # ------------------------------------------------------------ which shot
    def _wanted(self) -> Tuple[int, ...]:
        return tuple(FILTERS[self.filter][1])

    def _class(self, tag: int) -> int:
        return classify_one(self.src.flags(tag))

    def _set_order(self) -> None:
        """Offline: the shots of the current filter."""
        want = self._wanted()
        tags = self.src.tags or []
        keep = self.order[self.pos] if self.order else 0
        order = [i for i in range(len(tags)) if not want or int(self.src.cls[i]) in want]
        if not order:
            self.message = "no {} in this run: showing all shots".format(FILTERS[self.filter][0])
            self.filter = 0
            order = list(range(len(tags)))
        self.order = order
        self.pos = min(bisect.bisect_left(order, keep), len(order) - 1)

    def _live_tag(self) -> Optional[int]:
        """Live, not paused: the newest shot of the current filter."""
        want = self._wanted()
        tags = self.ring_tags
        if not tags:
            try:
                return int(self.src.newest_tag())
            except Exception:  # noqa: BLE001
                return self.tag
        if not want:
            return tags[-1]
        for t in reversed(tags[-12:]):        # flags arrive late: look at a few
            if self._class(t) in want:
                return t
        return self.tag

    def _live_step(self, n: int) -> None:
        """Live: n kept shots back (n < 0) or on, within the filter (pauses)."""
        self.paused = True
        tags = self.ring_tags
        if not tags:
            self.message = "no shots kept yet"
            return
        cur = self.tag if self.tag is not None else tags[-1]
        i = bisect.bisect_left(tags, cur)
        want = self._wanted()
        step = 1 if n > 0 else -1
        left = abs(n)
        tries = 0
        while left and 0 <= i + step < len(tags) and tries < 400:
            i += step
            tries += 1
            if not want or self._class(tags[i]) in want:
                left -= 1
        self.cursor = tags[max(0, min(i, len(tags) - 1))]
        if left:
            self.message = "end of the kept shots ({} kept)".format(len(tags))

    def _step(self, n: int) -> None:
        if self.live:
            self._live_step(n)
        elif self.order:
            self.pos = max(0, min(self.pos + n, len(self.order) - 1))

    def _jump(self, end: bool) -> None:
        if self.live:
            if self.ring_tags:
                self.paused = True
                self.cursor = self.ring_tags[-1 if end else 0]
        elif self.order:
            self.pos = len(self.order) - 1 if end else 0

    def _goto(self, text: str) -> None:
        try:
            n = int(text)
        except ValueError:
            self.message = "go to: not a number ({!r})".format(text)
            return
        if self.live:
            if not self.ring_tags:
                self.message = "no shots kept yet"
                return
            if n < 100000:
                self.message = "live: go to a tag (shot numbers only offline)"
                return
            self.paused = True
            self.cursor = min(self.ring_tags, key=lambda t: abs(t - n))
            if self.cursor != n:
                self.message = "tag {} not kept: nearest {}".format(n, self.cursor)
            return
        tags = self.src.tags or []
        if n >= 100000:            # a tag
            i = int(np.argmin(np.abs(np.array(tags) - n)))
            if i not in self.order:
                self.message = "tag {} is not one of the {}: showing all shots".format(tags[i], FILTERS[self.filter][0])
                self.filter = 0
                self._set_order()
            self.pos = self.order.index(i)
            if tags[i] != n:
                self.message = "tag {} not in the run: nearest {}".format(n, tags[i])
        else:                      # a shot number (1 = first of the filter)
            self.pos = max(0, min(n - 1, len(self.order) - 1))

    def current_tag(self) -> Optional[int]:
        if self.live:
            if self.paused and self.cursor is not None:
                return self.cursor
            return self._live_tag()
        if not self.order or not self.src.tags:
            return None
        return int(self.src.tags[self.order[self.pos]])

    # ------------------------------------------------------------ drawing
    def _load(self, tag: Optional[int]) -> None:
        """Image, flags and traces of the shot."""
        s = self.settings.s
        self.tag = tag
        if tag is None:
            return
        if tag != self.image_tag:
            img = self.src.read(tag)
            self.image_tag = tag
            if img is None:
                self.image = None
                self.message = self.message or ("image of tag {} no longer in the online buffer".format(tag)
                                                if self.live else "image of tag {} not readable".format(tag))
            else:
                self.image = img
        self.flags = self.src.flags(tag)
        roi, binning = s.get("roi"), s.get("binning", "rows")
        self.trace, self.axis = None, None
        if self.image is not None:
            self.trace = timetool.trace(self.image, roi, binning)
            self.axis = timetool.trace_axis(roi, self.image.shape, binning)
        elif tag in self.ring and self.src.shape is not None:
            self.trace = self.ring[tag][1].astype(np.float64)
            self.axis = timetool.trace_axis(roi, self.src.shape, binning)
        bg = s["background"]
        self.background.load(bg.get("file"))
        shape = self.image.shape if self.image is not None else self.src.shape
        self.bg_trace = self.background.trace(roi, binning, shape) if shape is not None else None
        self.corrected = None
        self.edge = {}
        if self.trace is not None and self.axis is not None:
            self.corrected = timetool.correct(self.trace, self.bg_trace, bg.get("mode", "divide"))
            target = self.corrected if self.corrected is not None else self.trace
            self.edge = timetool.find_edge(target, self.axis, s["edge"], details=True)
            if self.corrected is None:
                self.edge["good"] = False
                self.edge["why"] = "no background" + ("; " + self.edge["why"] if self.edge["why"] else "")

    def draw(self) -> None:
        self._load(self.current_tag())
        self._draw_image()
        self._draw_traces()
        self._status()
        self.help_text.set_visible(self.show_help)
        self.fig.canvas.draw_idle()

    def _draw_image(self) -> None:
        from matplotlib.colors import LogNorm, Normalize

        img = self.image
        if img is None:
            return
        d = self.settings.s["display"]
        log = bool(d.get("log"))
        vals = img[::7, ::7].ravel()
        if log:
            vals = vals[vals > 0]
        if d.get("auto_limits", True) or d.get("vmin") is None or d.get("vmax") is None:
            lo, hi = np.percentile(vals, d.get("percentiles", [1.0, 99.7])) if vals.size else (0.1, 1.0)
        else:
            lo, hi = float(d["vmin"]), float(d["vmax"])
        if hi <= lo:
            hi = lo + 1.0
        if log:
            lo = max(float(lo), 1e-3)
        self.lims = (float(lo), float(hi))
        shown = np.where(img > 0, img, np.nan) if log else img
        cmap = d.get("cmap", "viridis")
        kind = "{}|{}".format("log" if log else "lin", cmap)
        norm = LogNorm(vmin=lo, vmax=hi) if log else Normalize(vmin=lo, vmax=hi)
        if self.im is None or self.im.get_array().shape != img.shape:
            if self.im is not None:
                self.im.remove()
            self.im = self.ax.imshow(shown, cmap=cmap, norm=norm, interpolation="nearest", origin="upper",
                                     aspect="auto")
            self.ax.set_xlabel("x (camera pixel)")
            self.ax.set_ylabel("y (camera pixel)")
            self.norm_kind = kind
        else:
            self.im.set_data(shown)
            if kind != self.norm_kind:
                self.im.set_cmap(cmap)
                self.im.set_norm(norm)
                self.norm_kind = kind
            else:
                self.im.set_clim(lo, hi)
        s = self.settings.s
        x0, x1, y0, y1 = timetool.roi_box(s.get("roi"), img.shape)
        self.roi_patch.set_bounds(x0 - 0.5, y0 - 0.5, x1 - x0 + 1, y1 - y0 + 1)
        self.roi_patch.set_edgecolor("red" if self.editing else "orange")

    def _draw_traces(self) -> None:
        s = self.settings.s
        mode = s["background"].get("mode", "divide")
        along = "x" if s.get("binning", "rows") == "rows" else "y"
        if self.trace is None or self.axis is None:
            for line in (self.l_raw, self.l_bg, self.l_corr, self.l_smooth, self.l_fit):
                line.set_data([], [])
            for v in (self.v_raw, self.v_corr, self.v_der_raw, self.v_der):
                v.set_visible(False)
            self.edge_text.set_text("")
            return
        self._draw_edge()
        self.l_raw.set_data(self.axis, self.trace)
        bg_ok = self.bg_trace is not None and self.bg_trace.shape == self.trace.shape
        self.l_bg.set_data(self.axis if bg_ok else [], self.bg_trace if bg_ok else [])
        self.tax.relim()
        self.tax.autoscale_view()
        self.tax.set_xlim(float(self.axis[0]) - 0.5, float(self.axis[-1]) + 0.5)
        self.cax.set_xlabel("{} (camera pixel)   trace = mean over the ROI's {}".format(
            along, "rows" if along == "x" else "columns"))
        if self.corrected is not None:
            self.l_corr.set_data(self.axis, self.corrected)
            sm = self.edge.get("smooth")
            self.l_smooth.set_data(self.axis if sm is not None else [], sm if sm is not None else [])
            fx, fy = self.edge.get("fit_x"), self.edge.get("fit_y")
            self.l_fit.set_data(fx if fx is not None else [], fy if fy is not None else [])
            self.no_bg.set_text("")
            self.ref_line.set_ydata([1.0, 1.0] if mode == "divide" else [0.0, 0.0])
            self.ref_line.set_visible(True)
            fin = self.corrected[np.isfinite(self.corrected)]
            if fin.size:
                lo, hi = np.percentile(fin, [0.5, 99.5])
                pad = 0.1 * max(hi - lo, 1e-6)
                self.cax.set_ylim(lo - pad, hi + pad)
            self.cax.set_ylabel("shot / background" if mode == "divide" else "shot - background")
        else:
            for line in (self.l_corr, self.l_smooth, self.l_fit):
                line.set_data([], [])
            self.ref_line.set_visible(False)
            self.cax.set_ylabel("")
            bg = s["background"]
            self.no_bg.set_text(self.background.error or (
                "no background: set_tt_background(run=N) (HPC) or capture_tt_background() (beamline)"
                if not bg.get("file") else "background not usable"))

    def _draw_edge(self) -> None:
        """Vertical lines at the edge (solid: green good / red not) and the derivative's edge
        (dotted) in both trace plots, and the edge's numbers."""
        e = self.edge
        edge, der = float(e.get("edge", np.nan)), float(e.get("edge_deriv", np.nan))
        color = "C2" if e.get("good") else "C3"
        for v, x in ((self.v_raw, edge), (self.v_corr, edge), (self.v_der_raw, der), (self.v_der, der)):
            ok = bool(np.isfinite(x)) and not (v in (self.v_der_raw, self.v_der) and abs(x - edge) < 0.05)
            v.set_visible(ok)
            if ok:
                v.set_xdata([x, x])
                v.set_color(color)
        self.edge_text.set_text(self.edge_line())

    def edge_line(self) -> str:
        e = self.edge
        if not e or not np.isfinite(float(e.get("edge", np.nan))):
            return "edge: none" + (" ({})".format(e.get("why")) if e.get("why") else "")
        cal = self.settings.s.get("calibration")
        shift = timetool.edge_shift(float(e["edge"]), cal)
        corr = "   correction P(edge)-P(0) {:+.2f} {}".format(shift, cal.get("unit") or "pulse") \
            if cal and np.isfinite(shift) else "   (no calibration: calibrate_tt)"
        return "edge {:.1f} px ({})   derivative {:.1f}   step {:+.3g}   width {}   SNR {:.1f}   {}{}".format(
            float(e["edge"]), "fit" if np.isfinite(float(e.get("edge_fit", np.nan))) else "derivative",
            float(e["edge_deriv"]), float(e.get("step", np.nan)),
            "{:.0f} px".format(float(e["width"])) if np.isfinite(float(e.get("width", np.nan))) else "-",
            float(e.get("snr", np.nan)), "GOOD" if e.get("good") else "not good: " + str(e.get("why")), corr)

    def live_edges(self) -> str:
        """Live: the edges of the kept shots."""
        vals = [v for v, g in self.edges.values() if g and np.isfinite(v)]
        if not self.edges:
            return ""
        txt = "   last {} shots: {:.0f} % good edges".format(len(self.edges), 100.0 * len(vals) / len(self.edges))
        if len(vals) > 3:
            q = np.percentile(vals, [15.87, 50, 84.13])
            txt += ", median {:.1f} px, 16-84 %: {:.1f} .. {:.1f}".format(q[1], q[0], q[2])
        return txt

    def _status(self) -> None:
        s = self.settings.s
        bg = s["background"]
        img_shape = self.image.shape if self.image is not None else self.src.shape
        if self.live:
            n = max(self.n_timed, 1)
            where = "{} reader{}".format(self.n_readers, "s" if self.n_readers != 1 else "") if self.n_readers \
                else "one process"
            line1 = "{}   live, {}: {:.1f} shots/s, missed {}, per shot: read {:.1f} ms, trace {:.2f} ms, edge {:.2f} ms" \
                "   kept {}{}".format(self.src.label, where, self.rate(), self.missed, 1000 * self.t_read / n,
                                     1000 * self.t_trace / n, 1000 * self.t_edge / n, len(self.ring_tags),
                                     "   PAUSED" if self.paused else "")
        else:
            line1 = "{}   shot {} / {} ({}) of {} tags".format(
                self.src.label, self.pos + 1 if self.order else 0, len(self.order), FILTERS[self.filter][0],
                len(self.src.tags or []))
        cls = classify_one(self.flags) if self.tag is not None else timetool.OTHER
        line2 = "tag {}   run {}   {}   class: {}   showing: {}{}".format(
            self.tag, self.run, shot_flags_text(self.flags), timetool.CLASS_NAMES[cls], FILTERS[self.filter][0],
            self.live_edges() if self.live else "")
        if img_shape is not None:
            x0, x1, y0, y1 = timetool.roi_box(s.get("roi"), img_shape)
            roi_text = "x {}..{}, y {}..{} ({} x {} px{})".format(x0, x1, y0, y1, x1 - x0 + 1, y1 - y0 + 1,
                                                                  "" if s.get("roi") else ", whole image")
        else:
            roi_text = str(s.get("roi") or "whole image")
        along = "x" if s.get("binning", "rows") == "rows" else "y"
        if bg.get("file"):
            bg_text = "{} ({} shots), {}".format(bg.get("source") or os.path.basename(bg["file"]),
                                                 self.background.n, bg.get("mode", "divide"))
        else:
            bg_text = "none"
        line3 = "ROI {}   binning {} -> trace along {}   background: {}".format(
            roi_text, s.get("binning", "rows"), along, bg_text)
        ed = s["edge"]
        line3b = "edge: {}".format(timetool.edge_text(ed))
        line4 = "go to (tag or shot number, Enter / Esc): {}_".format(self.goto) if self.goto is not None \
            else ("ROI: drag a box in the image (e again: cancel)" if self.editing else self.message)
        self.status.set_text("\n".join([line1, line2, line3, line3b, line4]))

    # ------------------------------------------------------------ mouse
    def _roi_drawn(self, eclick: Any, erelease: Any) -> None:
        if eclick.xdata is None or erelease.xdata is None:
            return
        shape = self.image.shape if self.image is not None else self.src.shape
        x0, x1 = sorted((int(round(eclick.xdata)), int(round(erelease.xdata))))
        y0, y1 = sorted((int(round(eclick.ydata)), int(round(erelease.ydata))))
        if shape is not None:
            x0, x1, y0, y1 = timetool.roi_box([x0, x1, y0, y1], shape)
        self.settings.set("roi", [x0, x1, y0, y1])
        self.editing = False
        self.selector.set_active(False)
        self.message = "ROI set: x {}..{}, y {}..{}".format(x0, x1, y0, y1)
        self.settings_changed()
        self.draw()

    def on_scroll(self, event: Any) -> None:
        if event.inaxes is not self.ax or event.xdata is None:
            return
        f = 1 / 1.25 if event.button == "up" else 1.25
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        cx, cy = event.xdata, event.ydata
        self.ax.set_xlim(cx + (x0 - cx) * f, cx + (x1 - cx) * f)
        self.ax.set_ylim(cy + (y0 - cy) * f, cy + (y1 - cy) * f)
        self.fig.canvas.draw_idle()

    def reset_zoom(self) -> None:
        shape = self.image.shape if self.image is not None else self.src.shape
        if shape is not None:
            self.ax.set_xlim(-0.5, shape[1] - 0.5)
            self.ax.set_ylim(shape[0] - 0.5, -0.5)

    # ------------------------------------------------------------ keys
    def on_key(self, event: Any) -> None:
        k = event.key or ""
        if self.goto is not None:          # typing a tag / shot number
            if k in ("enter", "return"):
                text, self.goto = self.goto, None
                self.message = ""
                if text:
                    self._goto(text)
            elif k == "escape":
                self.goto = None
            elif k == "backspace":
                self.goto = self.goto[:-1]
            elif len(k) == 1 and k.isdigit():
                self.goto += k
            self.draw()
            return
        s = self.settings
        d = s.s["display"]
        self.message = ""
        if k == "h":
            self.show_help = not self.show_help
        elif k in (" ", "p"):
            if self.live:
                self.paused = not self.paused
                self.cursor = self.tag if self.paused else None
            else:
                self.message = "pause: live viewer only"
        elif k in ("left", "right"):
            self._step(-1 if k == "left" else 1)
        elif k in ("pageup", "pagedown"):
            self._step(-10 if k == "pageup" else 10)
        elif k in ("home", "end"):
            self._jump(k == "end")
        elif k == "g":
            self.goto = ""
        elif k == "f":
            self.filter = (self.filter + 1) % len(FILTERS)
            if not self.live:
                self._set_order()
            elif self.filter:
                self.message = "live: the flags reach the SyncDB a few seconds after the image"
        elif k == "b":
            s.set("binning", "cols" if s.s.get("binning") == "rows" else "rows")
            self.settings_changed()
        elif k == "e":
            self.editing = not self.editing
            self.selector.set_active(self.editing)
        elif k == "x":
            s.set("roi", None)
            self.settings_changed()
        elif k == "m":
            mode = s.s["background"].get("mode", "divide")
            s.set("background.mode", "subtract" if mode == "divide" else "divide")
        elif k == "d":
            cur = s.s["edge"].get("direction", "any")
            dirs = list(timetool.DIRECTIONS)
            s.set("edge.direction", dirs[(dirs.index(cur) + 1) % len(dirs)] if cur in dirs else "any")
        elif k == "k":
            s.set("edge.fit", not s.s["edge"].get("fit", True))
        elif k == "z":
            self.reset_zoom()
        elif k == "l":
            s.set("display.log", not d.get("log"))
        elif k in ("c", "C"):
            cm = d.get("cmap", "viridis")
            i = CMAPS.index(cm) if cm in CMAPS else 0
            s.set("display.cmap", CMAPS[(i + (1 if k == "c" else -1)) % len(CMAPS)])
        elif k == "v":
            if d.get("auto_limits", True):
                s.set("display.vmin", self.lims[0])
                s.set("display.vmax", self.lims[1])
                s.set("display.auto_limits", False)
                self.message = "colour limits frozen"
            else:
                s.set("display.auto_limits", True)
        elif k == "s":
            self.snapshot()
        elif k == "q":
            self.stop()
            return
        else:
            return
        self.draw()

    def snapshot(self) -> None:
        if self.trace is None or self.axis is None:
            self.message = "nothing to save"
            return
        folder = os.path.join(self.out_dir, "timetool", "snapshots")
        if not os.path.isdir(folder):
            os.makedirs(folder)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(folder, "tt_{}_tag{}".format(stamp, self.tag))
        self.fig.savefig(base + ".png", dpi=90)
        nan = np.full(len(self.trace), np.nan)
        bg = self.bg_trace if self.bg_trace is not None and self.bg_trace.shape == self.trace.shape else nan
        corr = self.corrected if self.corrected is not None else nan
        np.savez_compressed(base + ".npz", image=self.image if self.image is not None else np.zeros((0, 0)),
                            pixel=self.axis, trace=self.trace, background_trace=bg, corrected=corr,
                            tag=self.tag if self.tag is not None else -1, run=self.run if self.run is not None else -1,
                            flags=np.array(self.flags, float), settings=json.dumps(self.settings.s),
                            edge=json.dumps({k: (float(v) if isinstance(v, (float, np.floating)) else v)
                                             for k, v in self.edge.items() if k in timetool.EDGE_KEYS + ("why",)}))
        with open(base + ".csv", "w") as fh:
            fh.write("pixel,trace,background_trace,corrected\n")
            for row in zip(self.axis, self.trace, bg, corr):
                fh.write("{},{:.6g},{:.6g},{:.6g}\n".format(int(row[0]), row[1], row[2], row[3]))
        self.message = "saved " + base + ".png / .npz / .csv"

    def stop(self) -> None:
        self.running = False


# ====================================================================== main loops
def run_live(args: Any) -> None:
    from silas.core import mpi, plotting

    comm, rank, size, MPI = mpi.world()  # MPI only under mpirun (see core/mpi.py)
    settings = Settings()
    src = open_camera(settings.cfg, test=args.test, det_id=args.camera)
    step = detect_tag_step(src)

    if rank > 0:  # ------------------------------------------- reader
        reader = TraceReader(src, settings, wid=rank - 1, nworkers=size - 1, tag_step=step)
        next_report = time.time() + float(settings.s["display"]["update_s"])
        while True:
            if comm.Iprobe(source=0, tag=TAG_STOP):
                comm.recv(source=0, tag=TAG_STOP)
                break
            settings.refresh()
            busy = reader.step()
            if time.time() >= next_report:
                comm.send(reader.take(), dest=0, tag=TAG_DATA)
                next_report = time.time() + float(settings.s["display"]["update_s"])
            if not busy:
                time.sleep(0.002)
        return

    # ----------------------------------------------------------- display (rank 0)
    print("time-tool viewer: {} (tag step {}), {}".format(
        src.label, step, "{} MPI readers".format(size - 1) if size > 1 else "one process"), flush=True)
    viewer = TTViewer(settings, src, settings.cfg.output_dir(), live=True, n_readers=size - 1)
    local = TraceReader(src, settings, tag_step=step) if size == 1 else None
    state = {"next_draw": 0.0}
    print("Press h in the window for the keys.")

    def tick() -> bool:
        if settings.refresh():
            viewer.settings_changed()
        update_s = float(settings.s["display"]["update_s"])
        if local is not None:  # one process: read until it is time to draw
            t_end = time.time() + 0.6 * update_s
            while time.time() < t_end:
                if not local.step():
                    time.sleep(0.002)
            viewer.add(local.take())
        else:
            while comm.Iprobe(tag=TAG_DATA):
                viewer.add(comm.recv(tag=TAG_DATA))
        if time.time() >= state["next_draw"]:
            viewer.draw()
            state["next_draw"] = time.time() + update_s
        return bool(viewer.running)

    def stop() -> None:
        if comm is not None and size > 1:
            for r in range(1, size):
                comm.send(None, dest=r, tag=TAG_STOP)
        plotting.close_all(viewer.plt, [viewer.fig])
        print("viewer stopped")

    detached = False      # True: running on in a notebook, stop() is called when it ends
    try:
        detached = plotting.run_loop(viewer.plt, tick, 0.05, on_stop=stop)
    except KeyboardInterrupt:
        pass
    finally:
        if not detached:
            stop()


def run_offline(args: Any) -> None:
    from silas.core import plotting

    settings = Settings()
    src = open_camera(settings.cfg, run=args.run, det_id=args.camera)
    viewer = TTViewer(settings, src, settings.cfg.output_dir(), live=False)
    viewer.draw()
    viewer.reset_zoom()
    print("time-tool viewer: {}. Press h in the window for the keys.".format(src.label), flush=True)

    def tick() -> bool:
        if settings.refresh():
            viewer.settings_changed()
            viewer.draw()
        return bool(viewer.running)

    def stop() -> None:
        plotting.close_all(viewer.plt, [viewer.fig])
        print("viewer stopped")

    detached = False      # True: running on in a notebook, stop() is called when it ends
    try:
        detached = plotting.run_loop(viewer.plt, tick, 0.1, on_stop=stop)
    except KeyboardInterrupt:
        pass
    finally:
        if not detached:
            stop()


@sacla.requires_sacla
def run_sacla(args: Any) -> None:
    if args.run is not None:
        run_offline(args)
    else:
        run_live(args)


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="time-tool viewer: live (online) or a stored run (offline)")
    ap.add_argument("--run", type=int, help="a STORED run (HPC, stpy): step through its shots")
    ap.add_argument("--test", action="store_true", help="a made-up camera instead of the detector (live)")
    ap.add_argument("--camera", help="camera detector ID (default: the config's, else the first OPAL / IPX / CHET)")
    args = ap.parse_args(argv)
    if args.run is not None and args.test:
        print("offline viewer: ignored test (stored runs are read with stpy)")
        args.test = False
    try:
        if args.test:
            run_live(args)  # no SACLA library involved
        else:
            run_sacla(args)
    except KeyboardInterrupt:
        print("stopped (Ctrl-C)")


if __name__ == "__main__":
    main()
