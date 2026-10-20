"""Live viewer of an MPCCD array (e.g. the octal MPCCD), assembled in real space.

    start_mpccd_viewer()                                   # from the session (new terminal)
    mpirun -n 5 python -m silas.online.mpccd_view       # 1 display + 4 reader processes
    python -m silas.online.mpccd_view --test            # test data, one process, no SACLA
    python -m silas.online.mpccd_view --run 1234501     # STORED runs (HPC): offline view
    python -m silas.online.mpccd_view --run 1234501 --replay    # a stored run played as if live

With MPI, rank 0 shows the window and ranks 1..N read the detector: worker k
takes the shots with (tag / tag_step) mod N == k, corrects them (dark, the keV
threshold converted to ADU per panel) and sends its summed panels (ADU) and newest
shot to rank 0 every "update_s" seconds. Without MPI one process does both.
Rank 0 converts to keV (mp_absgain x calibration.ev_per_absgain_adu) only when it
draws; the histogram below the image is in keV, with the threshold and multiples of
the photon energy (config photon_energy_keV, else the run record) marked.
ROIs of the array (add_roi in the session) are drawn on the image with their size,
panels and mean keV per pixel of the shown image (silas.core.roi).

Offline view (--run N [N ...], HPC): all shots of the runs are loaded once and the
AVERAGE is shown, pump on or pump off (key w); dark / threshold changes recompute it
(silas.offline.run_cache). --replay instead plays the run as if live, in a loop.

All settings live in the array's entry in the config (silas.core.mpccd) and
the file is watched: a change made in the session (set_mpccd ...) or with a key
in the window reaches every process within about a second. Press h in the
window for the keys.
"""
import argparse
import datetime
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import energy, mpccd, roi, sacla
from silas.core.config import Config
from silas.core.corrections import Corrections, settings_id  # noqa: F401
from silas.core.geometry import Geometry
from silas.online.mpccd_source import open_source

TAG_DATA = 11
TAG_STOP = 12

HELP = """keys
 h        this help on / off
 a        average / latest shot   (online)
 r        reset the average       (online)
 p        pause the display       (online)
 <  >     slower / faster updates (online)
 z        reset zoom (mouse wheel: zoom)
 l        log / linear colour scale
 c  C     next / previous colour map
 v        freeze / auto colour limits
 o        panel outlines on / off
 i        ROIs on / off
 w        pump on / off           (offline)
 s        save snapshot (png + npz)
 q        quit

corrections
 d        dark subtraction on / off
 t        threshold on / off
 +  -     threshold x1.25 / /1.25 (keV)
 u        units keV / raw ADU

geometry tuning
 1 ... 8  select panel  (0: none)
 arrows   move panel 1 pixel
 shift+arrows  10 pixels
 [  ]     rotate panel -/+ 0.05 deg
 m  M     aperture shift -/+ 100 um
 x        clear tuning of the panel
          (all panels if none selected)"""


# ====================================================================== settings
class Settings(object):
    """The array's settings, re-read whenever the config file changes."""

    def __init__(self, name: Optional[str]) -> None:
        self.cfg = Config()
        self.name = name
        self.stamp = None  # type: Any
        self.key = ""
        self.s = {}  # type: Dict[str, Any]
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> bool:
        """Reload if the file changed. Returns True if it did."""
        stamp = self.cfg.file_stamp()
        if stamp is None or (not force and stamp == self.stamp):
            return False
        self.stamp = stamp
        try:
            self.cfg.reload()
            self.key, self.s = mpccd.get_array(self.cfg, self.name)
        except (ValueError, KeyError) as err:  # half-edited file etc.: keep the old settings
            print("config not reloaded:", err)
            return False
        return True

    def set(self, key: str, value: Any) -> None:
        mpccd.set_option(self.cfg, key, value, self.key)
        self.refresh(force=True)

    def get(self, key: str) -> Any:
        node = self.s  # type: Any
        for p in key.split("."):
            node = node[p]
        return node


def kev_per_adu(settings: "Settings", infos: List[Dict[str, Any]]) -> np.ndarray:
    """keV per ADU of every panel with the array's current calibration."""
    return energy.kev_per_adu(energy.absgains(infos), energy.ev_per_absgain_adu(settings.s))


def corrections_id(settings: "Settings", infos: List[Dict[str, Any]]) -> str:
    """Id of everything that changes the corrected shots (corrections + calibration)."""
    return settings_id(settings.s["corrections"], kev_per_adu(settings, infos))


# ====================================================================== reading
class Reader(object):
    """Reads, corrects and sums the shots of one worker."""

    def __init__(self, source: Any, settings: Settings, wid: int = 0, nworkers: int = 1) -> None:
        self.src = source
        self.settings = settings
        self.wid = wid
        self.nw = nworkers
        self.infos = source.infos()
        self.corr = None  # type: Optional[Corrections]
        self.last_tag = None  # type: Optional[int]
        self.missed = 0
        self.reset()

    def reset(self) -> None:
        self.sum = None  # type: Optional[np.ndarray]
        self.n = 0
        self.latest = None  # type: Optional[np.ndarray]
        self.latest_tag = None  # type: Optional[int]

    def corrections(self) -> Corrections:
        wanted = corrections_id(self.settings, self.infos)
        if self.corr is None or self.corr.id != wanted:
            self.corr = Corrections(self.settings.s["corrections"], kev_per_adu(self.settings, self.infos),
                                    self.src.det_ids)
            for m in self.corr.messages:
                print(m)
            self.reset()  # never mix shots with different corrections
        return self.corr

    def step(self) -> bool:
        """Process the newest shot of this worker if there is a new one."""
        step = int(self.settings.s.get("tag_step", 1))
        newest = self.src.newest_tag()
        slot = (newest // step) % self.nw
        target = newest - ((slot - self.wid) % self.nw) * step
        if self.last_tag is not None and target <= self.last_tag:
            return False
        if self.last_tag is not None:
            self.missed += max(0, (target - self.last_tag) // (step * self.nw) - 1)
        self.last_tag = target
        images = self.src.read(target)
        if images is None:
            self.missed += 1
            return True
        x = self.corrections().apply(images)
        self.sum = x.astype(np.float64) if self.sum is None else self.sum + x
        self.n += 1
        self.latest, self.latest_tag = x, target
        return True

    def take(self) -> Dict[str, Any]:
        """Everything since the last take(), then start a new partial sum."""
        real = getattr(self.src, "real_tag", None)    # a replay: its passes count tags on
        shown = real(self.latest_tag) if real is not None and self.latest_tag is not None else self.latest_tag
        msg = {"sum": self.sum, "n": self.n, "latest": self.latest, "latest_tag": self.latest_tag,
               "shown_tag": shown,
               "missed": self.missed, "corr_id": self.corr.id if self.corr else None,
               "run": getattr(self.src, "run", None)}
        self.reset()
        self.missed = 0
        return msg


# ====================================================================== display
def _pyplot(wanted: Optional[str] = None) -> Any:
    """pyplot with a window backend (silas.core.plotting.pyplot; in a notebook the widget)."""
    from silas.core import plotting

    return plotting.pyplot(wanted, interactive=True)


class Viewer(object):
    def __init__(self, settings: Settings, infos: List[Dict[str, Any]], det_ids: List[str],
                 source_label: str, out_dir: str) -> None:
        import matplotlib

        plt = _pyplot(settings.s["display"].get("backend"))
        for k in list(matplotlib.rcParams):  # our keys, not matplotlib's shortcuts
            if k.startswith("keymap."):
                matplotlib.rcParams[k] = []
        self.plt = plt
        self.settings = settings
        self.infos = infos
        self.det_ids = det_ids
        self.source_label = source_label
        self.out_dir = out_dir
        self.selected = 0  # panel being tuned (1-based), 0 = none
        self.paused = False
        self.show_help = True
        self.running = True
        self.sum = None  # type: Optional[np.ndarray]
        self.n = 0
        self.latest = None  # type: Optional[np.ndarray]
        self.latest_tag = None  # type: Optional[int]
        self.shown_tag = None  # type: Optional[int]
        self.corr_id = corrections_id(settings, infos)
        self.run = None  # type: Optional[int]
        self.photon_keV = None  # type: Optional[float]
        self.received = []  # type: List[Tuple[float, int]]
        self.missed = 0
        self.message = ""
        self.image = None  # type: Optional[np.ndarray]
        self.shown = None  # type: Optional[np.ndarray]
        self.lims = (0.0, 1.0)
        self.ax = None  # type: Any
        self.im = None  # type: Any
        self.geom_key = ""
        self.geom = self._geometry()

        from silas.core import plotting

        plotting.ion(plt)
        self.fig = plt.figure(figsize=(14, 11))
        manager = self.fig.canvas.manager
        if manager is not None:
            try:
                manager.set_window_title("MPCCD viewer - " + settings.key)
            except AttributeError:
                pass
        gs = matplotlib.gridspec.GridSpec(2, 3, figure=self.fig, width_ratios=[20, 1, 7],
                                          height_ratios=[4, 1], wspace=0.15, hspace=0.25)
        self.ax = self.fig.add_subplot(gs[0, 0])
        self.cax = self.fig.add_subplot(gs[0, 1])
        self.hax = self.fig.add_subplot(gs[1, 0])
        self.tax = self.fig.add_subplot(gs[:, 2])
        self.tax.axis("off")
        self.help_text = self.tax.text(0.0, 1.0, HELP, va="top", family="monospace", fontsize=8.5,
                                       transform=self.tax.transAxes)
        self.status = self.fig.text(0.01, 0.985, "", va="top", family="monospace", fontsize=9)
        self.cbar = None  # type: Any
        self.norm_kind = ""
        self.outline_lines = []  # type: List[Any]
        self.roi_artists = []  # type: List[Any]
        self.roi_map = None  # type: Optional[roi.RoiMap]
        self.roi_key = ""
        self.roi_text = self.tax.text(0.0, 0.0, "", va="bottom", family="monospace", fontsize=8.5,
                                      transform=self.tax.transAxes)
        self.hist_line = None  # type: Any
        self.hist_marks = []  # type: List[Any]
        # single-shot histogram (keV), added up in average mode
        self.hist_counts = None  # type: Optional[np.ndarray]
        self.hist_range = None  # type: Any
        self.hist_shots = 0
        self.hist_tag = None  # type: Optional[int]
        # offline view of stored runs (silas.offline.run_cache.OfflineRuns), None: live
        self.static = None  # type: Any
        self.pump_view = 0  # 0 pump on, 1 pump off
        self.home = None  # type: Any
        self.fig.canvas.mpl_connect("key_press_event", self.on_key)
        self.fig.canvas.mpl_connect("scroll_event", self.on_scroll)
        self.fig.canvas.mpl_connect("close_event", lambda e: self.stop())

    # ------------------------------------------------------------ data
    def _geometry(self) -> Geometry:
        g = self.settings.s["geometry"]
        key = json.dumps(g, sort_keys=True)
        if key != self.geom_key:
            self.geom_key = key
            base = Geometry.from_infos(self.det_ids, self.infos,
                                       manipulator_shift_um=float(g.get("manipulator_shift_um", 0.0)),
                                       rounding=g.get("rounding", "rint"),
                                       sub_pixel=tuple(g.get("sub_pixel", (0.0, 0.0))))
            self.geom = base.tweaked(panel_offsets=g.get("panel_offsets") or {})
            self.outline_lines = []
            self.roi_artists = []
            if self.ax is not None:
                self.home = (self.ax.get_xlim(), self.ax.get_ylim()) if self.im is not None else None
                self.ax.cla()
                self.im = None
        return self.geom

    def reset(self) -> None:
        self.sum, self.n = None, 0
        self.hist_counts, self.hist_shots, self.hist_tag = None, 0, None
        self.message = "average reset"

    def add(self, msg: Dict[str, Any]) -> None:
        if msg.get("run") is not None:
            self.run = int(msg["run"])
        if msg.get("corr_id") != self.corr_id:
            return  # made with other corrections
        if msg.get("n"):
            self.sum = msg["sum"] if self.sum is None else self.sum + msg["sum"]
            self.n += int(msg["n"])
            self.received.append((time.time(), int(msg["n"])))
        if msg.get("latest") is not None and (self.latest_tag is None or msg["latest_tag"] > self.latest_tag):
            self.latest, self.latest_tag = msg["latest"], msg["latest_tag"]
            self.shown_tag = msg.get("shown_tag", msg["latest_tag"])
        self.missed += int(msg.get("missed", 0))

    def settings_changed(self) -> None:
        cid = corrections_id(self.settings, self.infos)
        if cid != self.corr_id:
            self.corr_id = cid
            self.reset()
            self.latest, self.latest_tag = None, None
            self.message = "corrections changed: average reset"
        self._geometry()

    def _static_update(self) -> None:
        """Offline view: the averages for the current corrections (started if needed)."""
        rng = self._hist_range()
        key = self.static.request(self.settings.s["corrections"], kev_per_adu(self.settings, self.infos), rng)
        res = self.static.result(key)
        if res is None:          # computing: keep showing the previous image
            return
        s = self.pump_view
        self.n = int(res["n"][s])
        self.sum = res["sums"][s] if self.n else None
        self.hist_counts, self.hist_range, self.hist_shots = res["hist"][s], rng, self.n

    def rate(self) -> float:
        now = time.time()
        self.received = [(t, n) for t, n in self.received if now - t < 10.0]
        if not self.received:
            return 0.0
        span = max(now - self.received[0][0], self.settings.s["display"]["update_s"])
        return sum(n for _, n in self.received) / span

    # ------------------------------------------------------------ drawing
    def draw(self) -> None:
        from matplotlib.colors import LogNorm, Normalize

        d = self.settings.s["display"]
        self.photon_keV = energy.photon_energy(self.settings.cfg, self.run)
        if self.static is not None:
            self._static_update()
        if self.static is not None:
            if self.sum is not None and self.n:
                self.shown = (self.sum / self.n).astype(np.float32)
        elif not self.paused:  # paused: keep the panels, but re-assemble (geometry may change)
            if d.get("mode") == "latest" and self.latest is not None:
                self.shown = self.latest
            elif self.sum is not None and self.n:
                self.shown = (self.sum / self.n).astype(np.float32)
            else:
                self.shown = self.latest
        if self.shown is not None:  # panels in ADU; to keV here, once per display update
            scale = kev_per_adu(self.settings, self.infos) if self.kev_units() else None
            self.image = self.geom.assemble(list(self.shown), gain=False, scale=scale)
        img = self.image
        if img is None:
            self._status()
            self.fig.canvas.draw_idle()
            return

        vals = img[np.isfinite(img)][::31]
        log = bool(d.get("log"))
        if log:
            vals = vals[vals > 0]
        if d.get("auto_limits", True) or d.get("vmin") is None or d.get("vmax") is None:
            if vals.size:
                lo, hi = np.percentile(vals, d.get("percentiles", [1.0, 99.7]))
            else:
                lo, hi = 0.1, 1.0
        else:
            lo, hi = float(d["vmin"]), float(d["vmax"])
        if hi <= lo:
            hi = lo + 1.0
        if log:
            lo = max(lo, 1e-3)
        self.lims = (float(lo), float(hi))
        shown = np.where(img > 0, img, np.nan) if log else img
        kind = "{}|{}".format("log" if log else "lin", d.get("cmap", "viridis"))
        if self.im is None:
            norm = LogNorm(vmin=lo, vmax=hi) if log else Normalize(vmin=lo, vmax=hi)
            self.im = self.ax.imshow(shown, extent=self.geom.extent_mm(), cmap=self._cmap(d.get("cmap", "viridis")),
                                     norm=norm, interpolation="nearest", origin="upper")
            self.im.format_cursor_data = lambda data: ""  # the value is in format_coord
            self.ax.format_coord = self.format_coord
            self.ax.set_xlabel("X (mm)")
            self.ax.set_ylabel("Y (mm)")
            self.ax.set_autoscale_on(False)  # keep the zoom when outlines are redrawn
            if self.home is not None:  # geometry changed: keep the user's zoom
                self.ax.set_xlim(self.home[0])
                self.ax.set_ylim(self.home[1])
            self.cax.cla()
            self.cbar = self.fig.colorbar(self.im, cax=self.cax)
            self.norm_kind = kind
            self.units_shown = ""
        else:
            self.im.set_data(shown)
            if kind != self.norm_kind:  # log/linear or colour map changed: new norm
                self.im.set_cmap(self._cmap(d.get("cmap", "viridis")))
                self.im.set_norm(LogNorm(vmin=lo, vmax=hi) if log else Normalize(vmin=lo, vmax=hi))
                self.cbar.update_normal(self.im)
                self.norm_kind = kind
            else:
                self.im.set_clim(lo, hi)
        units = "keV" if self.kev_units() else "ADU"
        if getattr(self, "units_shown", "") != units:
            self.cbar.set_label(units)
            self.units_shown = units
        self._outlines(bool(d.get("outlines", True)))
        self._rois(bool(d.get("rois", True)))
        self._histogram(img)
        self._status()
        self.help_text.set_visible(self.show_help)
        self.fig.canvas.draw_idle()

    # ------------------------------------------------------------ mouse
    def format_coord(self, x: float, y: float) -> str:
        """Text in the toolbar when the mouse is over the image."""
        row, col = self.geom.pixel_of(x, y)
        cr, cc = self.geom.centre
        text = "X={:8.3f} mm  Y={:8.3f} mm   pixel (row {}, col {}) from origin".format(x, y, row - cr, col - cc)
        hit = self.geom.lookup(row, col)
        if hit is None:
            return text + "   gap"
        k, prow, pcol = hit
        value = ""
        if self.image is not None:
            value = "   value {:.6g} {}".format(float(self.image[row, col]), "keV" if self.kev_units() else "ADU")
        return text + "   panel {} (row {}, col {}){}".format(k + 1, prow, pcol, value)

    def on_scroll(self, event: Any) -> None:
        """Mouse wheel: zoom in / out around the cursor."""
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
        ext = self.geom.extent_mm()
        self.ax.set_xlim(ext[0], ext[1])
        self.ax.set_ylim(ext[2], ext[3])

    def _cmap(self, name: str) -> Any:
        import copy

        try:
            cmap = copy.copy(self.plt.get_cmap(name))
        except ValueError:
            cmap = copy.copy(self.plt.get_cmap("viridis"))
        cmap.set_bad("0.85")  # panel gaps: light grey
        return cmap

    def current_roi_map(self) -> Optional[roi.RoiMap]:
        """RoiMap for the current ROIs and geometry (rebuilt when either changes)."""
        rois = self.settings.s.get("rois") or {}
        key = json.dumps(rois, sort_keys=True) + "|" + self.geom_key
        if key != self.roi_key:
            self.roi_key = key
            self.roi_map = roi.RoiMap(self.geom, rois) if rois else None
        return self.roi_map

    def _rois(self, on: bool) -> None:
        """Draw the ROI rectangles and list size, panels and mean keV per pixel of the shown image."""
        for a in self.roi_artists:
            a.remove()
        self.roi_artists = []
        rmap = self.current_roi_map()
        if not on:
            self.roi_text.set_text("")
            return
        if rmap is None:
            alias = self.settings.cfg.get("detectors", {}).get(self.settings.key, {}).get("alias") or self.settings.key
            self.roi_text.set_text("no ROIs on this array ({}):\nadd_roi(name, rows=(r0, r1), cols=(c0, c1))\n"
                                   "in the session (it uses this array)".format(alias))
            return
        p = self.geom.pixel_um / 1000.0
        means = None
        if self.shown is not None and rmap.panels:
            scale = kev_per_adu(self.settings, self.infos) if self.kev_units() else np.ones(len(self.det_ids))
            sums = rmap.sums({k: self.shown[k] for k in rmap.panels}, scale)  # shown: already corrected
            means = rmap.means(sums)
        unit = "keV" if self.kev_units() else "ADU"
        lines = ["ROIs ({} per pixel, {})".format(unit, "latest" if self.settings.s["display"].get("mode") == "latest"
                                                   else "average"),
                 "{:<12s} {:>7s} {:>8s} {:>10s}".format("name", "pixels", "panels", "mean")]
        for i, (name, rect) in enumerate(zip(rmap.names, rmap.rects)):
            (r0, r1), (c0, c1) = rect["rows"], rect["cols"]
            x0, x1 = (c0 - 0.5) * p, (c1 + 0.5) * p          # ROIs are relative to the origin
            y0, y1 = -(r1 + 0.5) * p, -(r0 - 0.5) * p
            empty = rmap.n_pixels[i] == 0
            color = "0.6" if empty else "orange"
            self.roi_artists += self.ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], "-", color=color, lw=1.2)
            self.roi_artists.append(self.ax.text(x0, y1, " " + name, color=color, fontsize=8, va="bottom"))
            panels = ",".join(str(k + 1) for k, _ in rmap.parts[i]) or "-"
            value = "{:.4g}".format(means[i]) if means is not None and not empty else "-"
            lines.append("{:<12s} {:>7d} {:>8s} {:>10s}".format(name[:12], rmap.n_pixels[i], panels, value))
        lines.append("panels to read: {} of {}".format(
            ",".join(str(k + 1) for k in rmap.panels) or "none", len(self.det_ids)))
        self.roi_text.set_text("\n".join(lines))

    def _outlines(self, on: bool) -> None:
        for line in self.outline_lines:
            line.remove()
        self.outline_lines = []
        if not on and not self.selected:
            return
        for k, poly in enumerate(self.geom.outlines_mm()):
            sel = (k + 1) == self.selected
            if not on and not sel:
                continue
            self.outline_lines += self.ax.plot(poly[:, 0], poly[:, 1], "-", color="red" if sel else "white",
                                               lw=1.5 if sel else 0.6)
            self.outline_lines.append(self.ax.text(poly[0, 0], poly[0, 1], str(k + 1), fontsize=9,
                                                   color="red" if sel else "white"))

    def kev_units(self) -> bool:
        return str(self.settings.s["display"].get("units", "keV")).lower() == "kev"

    def _hist_range(self) -> Any:
        """(bins, lo, hi) of the keV histogram: fixed, so single shots can be added up."""
        e = self.photon_keV
        if e:
            return 325, -0.25 * e, 3.0 * e          # bin = 1 % of the photon energy
        return 420, -2.0, 40.0                       # photon energy unknown: 0.1 keV bins

    def _single_shot_histogram(self) -> None:
        """Add the newest single shot (keV) to the histogram (latest mode: replace it)."""
        if self.latest is None or self.latest_tag == self.hist_tag:
            return
        rng = self._hist_range()
        if rng != self.hist_range:
            self.hist_counts, self.hist_shots, self.hist_range = None, 0, rng
        kev = kev_per_adu(self.settings, self.infos)
        counts = np.zeros(rng[0], np.int64)
        for k, panel in enumerate(self.latest):
            if np.isfinite(kev[k]):
                c, _ = np.histogram(panel.ravel()[::2] * kev[k], bins=rng[0], range=(rng[1], rng[2]))
                counts += c
        if self.settings.s["display"].get("mode") == "latest" or self.hist_counts is None:
            self.hist_counts, self.hist_shots = counts, 1
        else:
            self.hist_counts = self.hist_counts + counts
            self.hist_shots += 1
        self.hist_tag = self.latest_tag

    def _histogram(self, img: np.ndarray) -> None:
        """keV: histogram of SINGLE shots (the newest one; in average mode all shots shown since
        the last reset added up), threshold (red) and multiples of the photon energy (green).
        A histogram of the averaged image would show no photon peaks.
        ADU: the shown image's raw values (panels not calibrated)."""
        corr = self.settings.s["corrections"]
        kev = self.kev_units()
        e = self.photon_keV
        average = self.settings.s["display"].get("mode") != "latest"
        if kev:
            if self.static is None:
                self._single_shot_histogram()
            if self.hist_counts is None:
                return
            nb, lo, hi = self.hist_range
            edges = np.linspace(lo, hi, nb + 1)
            counts = self.hist_counts
        else:
            vals = img[np.isfinite(img)][::3]
            if not vals.size:
                return
            lo, hi = np.percentile(vals, [0.05, 99.95])
            lo, hi = min(lo, self.lims[0]), max(hi, self.lims[1])
            if hi <= lo:
                hi = lo + 1.0
            counts, edges = np.histogram(vals, bins=300, range=(lo, hi))
        counts = np.maximum(counts, 0.5)  # log axis
        x = np.repeat(edges, 2)[1:-1]
        y = np.repeat(counts, 2)
        if self.hist_line is None:
            (self.hist_line,) = self.hax.plot(x, y, color="0.3", lw=1)
            self.hax.set_yscale("log")
            self.hax.set_ylabel("pixels")
        else:
            self.hist_line.set_data(x, y)
        for m in self.hist_marks:
            m.remove()
        self.hist_marks = []
        if kev:
            what = "{} single shots added".format(self.hist_shots) if average and self.hist_shots > 1 \
                else "single shot"
            self.hax.set_xlabel("deposited energy per pixel (keV), {}   red: threshold   green: n x photon energy{}".format(
                what, "" if e else " (photon energy unknown)"))
            if corr.get("threshold"):
                self.hist_marks.append(self.hax.axvline(float(corr.get("threshold_keV", 0.0)), color="red", lw=1.2))
            if e:
                for n in (1, 2, 3):
                    self.hist_marks.append(self.hax.axvline(n * e, color="C2", ls=":", lw=1.0))
            if not average:
                self.hist_marks += [self.hax.axvline(v, color="C0", ls="--", lw=1) for v in self.lims]
        else:
            self.hax.set_xlabel("raw ADU of each panel (not calibrated), {}   dashed: colour limits".format(
                "average of shots" if average else "single shot"))
            self.hist_marks += [self.hax.axvline(v, color="C0", ls="--", lw=1) for v in self.lims]
        self.hax.set_xlim(lo, hi)
        self.hax.set_ylim(0.5, max(2.0, float(counts.max()) * 2))

    def _status(self) -> None:
        d = self.settings.s["display"]
        g = self.settings.s["geometry"]
        c = self.settings.s["corrections"]
        t = float(c.get("threshold_keV", 0.0))
        e = self.photon_keV
        over = energy.photon_energy_override(self.settings.cfg)
        tuned = ", ".join(sorted((g.get("panel_offsets") or {}).keys()))
        line2 = "tag {}   averaged {} shots   {:.1f} shots/s   missed {}   update {:g} s   colour {} {}".format(
            self.shown_tag, self.n, self.rate(), self.missed, float(d.get("update_s", 0.2)),
            d.get("cmap"), "log" if d.get("log") else "lin")
        if self.static is not None:
            line2 = "{}   colour {} {}".format(self.static.describe(self.pump_view), d.get("cmap"),
                                                "log" if d.get("log") else "lin")
        lines = [
            "{}  [{}]   source: {}   {}{}".format(
                self.settings.key, self.settings.cfg.get("detectors", {}).get(self.settings.key, {}).get("alias", ""),
                self.source_label, "average" if self.static is not None else d.get("mode"),
                "   PAUSED" if self.paused else ""),
            line2,
            "corrections: dark {} ({}), threshold {}   units {}".format(
                "on" if c.get("dark") else "off", os.path.basename(c.get("dark_file") or "no file"),
                "{:g} keV{}".format(t, " = {:.2f} photons".format(t / e) if e else "")
                if c.get("threshold") else "off ({:g} keV)".format(t), d.get("units", "keV")),
            "energy: photon {}   keV/ADU = absgain x {:g} eV   1 photon = {} ADU".format(
                "{:g} keV ({})".format(e, "config" if over is not None else "run {}".format(self.run))
                if e else "unknown (set_photon_energy(keV) or run record)",
                energy.ev_per_absgain_adu(self.settings.s),
                "/".join("{:.0f}".format(e / float(k)) for k in kev_per_adu(self.settings, self.infos)) if e else "-"),
            "geometry: aperture shift {:g} um   tuned panels: {}   selected: {}   {}".format(
                float(g.get("manipulator_shift_um", 0.0)), tuned or "none", self.selected or "-", self.message),
        ]
        self.status.set_text("\n".join(lines))

    # ------------------------------------------------------------ keys
    def on_key(self, event: Any) -> None:
        k = event.key or ""
        s = self.settings
        d, c = s.s["display"], s.s["corrections"]
        self.message = ""
        if k == "h":
            self.show_help = not self.show_help
        elif self.static is not None and k in ("a", "r", "p", "<", ">"):
            self.message = "{}: online viewer only (offline: always the average of all loaded shots)".format(k)
        elif k == "w":
            if self.static is None:
                self.message = ("w: pump on / off is in the offline viewer only (live: the pump flag "
                                "reaches the SyncDB seconds after the image)")
            elif not self.static.has_pump:
                self.message = "no pump detector in the config (scan.pump_detector): all shots are shown"
            else:
                self.pump_view = 1 - self.pump_view
        elif k == "a":
            s.set("display.mode", "latest" if d.get("mode") == "average" else "average")
        elif k == "r":
            self.reset()
        elif k == "p":
            self.paused = not self.paused
        elif k == "z":
            self.reset_zoom()
        elif k in ("<", ">"):
            u = float(d.get("update_s", 0.2)) * (2.0 if k == "<" else 0.5)
            s.set("display.update_s", round(min(max(u, 0.05), 5.0), 3))
        elif k == "l":
            s.set("display.log", not d.get("log"))
        elif k in ("c", "C"):
            cm = d.get("cmap", "viridis")
            i = mpccd.CMAPS.index(cm) if cm in mpccd.CMAPS else 0
            s.set("display.cmap", mpccd.CMAPS[(i + (1 if k == "c" else -1)) % len(mpccd.CMAPS)])
        elif k == "v":
            if d.get("auto_limits", True):
                s.set("display.vmin", self.lims[0])
                s.set("display.vmax", self.lims[1])
                s.set("display.auto_limits", False)
                self.message = "colour limits frozen"
            else:
                s.set("display.auto_limits", True)
        elif k == "o":
            s.set("display.outlines", not d.get("outlines", True))
        elif k == "i":
            s.set("display.rois", not d.get("rois", True))
        elif k == "s":
            self.snapshot()
        elif k == "q":
            self.stop()
        elif k == "d":
            s.set("corrections.dark", not c.get("dark"))
            if not c.get("dark_file"):
                self.message = "no dark file yet: capture_dark() in the session"
        elif k == "u":
            s.set("display.units", "ADU" if self.kev_units() else "keV")
            s.set("display.auto_limits", True)  # frozen limits are in the old units
        elif k == "t":
            s.set("corrections.threshold", not c.get("threshold"))
        elif k in ("+", "=", "-"):
            f = 1.25 if k != "-" else 1 / 1.25
            s.set("corrections.threshold_keV", round(float(c.get("threshold_keV", 1.0)) * f, 4))
        elif len(k) == 1 and k in "012345678":
            n = int(k)
            self.selected = n if n <= len(self.det_ids) else 0
        elif k.replace("shift+", "") in ("left", "right", "up", "down") and self.selected:
            step = (10 if k.startswith("shift+") else 1) * self.geom.pixel_um
            dx = {"left": -step, "right": step}.get(k.replace("shift+", ""), 0.0)
            dy = {"up": step, "down": -step}.get(k.replace("shift+", ""), 0.0)
            self._tune(dx_um=dx, dy_um=dy)
        elif k in ("[", "]") and self.selected:
            self._tune(drot_deg=0.05 if k == "]" else -0.05)
        elif k in ("m", "M"):
            g = float(s.s["geometry"].get("manipulator_shift_um", 0.0))
            s.set("geometry.manipulator_shift_um", g + (100.0 if k == "M" else -100.0))
        elif k == "x":
            offs = dict(s.s["geometry"].get("panel_offsets") or {})
            if self.selected:
                offs.pop(str(self.selected), None)
            else:
                offs = {}
            s.set("geometry.panel_offsets", offs)
        else:
            return
        self.settings_changed()
        self.draw()

    def _tune(self, dx_um: float = 0.0, dy_um: float = 0.0, drot_deg: float = 0.0) -> None:
        offs = json.loads(json.dumps(self.settings.s["geometry"].get("panel_offsets") or {}))
        o = offs.setdefault(str(self.selected), {"dx_um": 0.0, "dy_um": 0.0, "drot_deg": 0.0})
        o["dx_um"] = round(o.get("dx_um", 0.0) + dx_um, 3)
        o["dy_um"] = round(o.get("dy_um", 0.0) + dy_um, 3)
        o["drot_deg"] = round(o.get("drot_deg", 0.0) + drot_deg, 4)
        self.settings.set("geometry.panel_offsets", offs)

    def snapshot(self) -> None:
        if self.image is None:
            return
        folder = os.path.join(self.out_dir, "mpccd")
        if not os.path.isdir(folder):
            os.makedirs(folder)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if self.static is not None:     # offline view: the runs and the pump state shown
            what = "runs{}_{}".format("-".join(str(r) for r in self.static.runs),
                                      "on" if self.pump_view == 0 else "off")
        else:
            what = "tag{}".format(self.shown_tag)
        base = os.path.join(folder, "snapshot_{}_{}".format(stamp, what))
        self.fig.savefig(base + ".png", dpi=90)
        np.savez_compressed(base + ".npz", image=self.image, n_shots=self.n, tag=self.shown_tag or -1,
                            extent_mm=np.array(self.geom.extent_mm()), settings=json.dumps(self.settings.s),
                            det_ids=np.array(self.det_ids))
        self.message = "saved " + base + ".png/.npz"

    def stop(self) -> None:
        self.running = False


# ====================================================================== main loops
def run_viewer(args: Any) -> None:
    from silas.core import mpi

    comm, rank, size, MPI = mpi.world()  # MPI only under mpirun (see core/mpi.py)

    settings = Settings(args.array)
    runs = list(getattr(args, "run", None) or [])
    if runs and not getattr(args, "replay", False):
        run_offline(args, settings, runs)
        return
    if len(runs) > 1:
        print("replay: one run at a time, run {} (not {})".format(runs[0], ", ".join(str(r) for r in runs[1:])))
    replay = runs[0] if runs else None
    det_ids = list(settings.s["panels"])
    tag_step = int(settings.s.get("tag_step", 1))
    source = open_source(det_ids, args.test, args.test_data, tag_step,
                         photon_keV=energy.photon_energy_override(settings.cfg),
                         ev_per_absgain_adu=energy.ev_per_absgain_adu(settings.s),
                         run=replay, bl=settings.cfg.beamline,
                         rate_hz=float(getattr(args, "rate", 10.0) or 10.0))
    det_ids = list(source.det_ids)

    if rank > 0:  # ------------------------------------------- reader
        reader = Reader(source, settings, wid=rank - 1, nworkers=size - 1)
        next_report = time.time() + settings.s["display"]["update_s"]
        while True:
            if comm.Iprobe(source=0, tag=TAG_STOP):
                comm.recv(source=0, tag=TAG_STOP)
                break
            settings.refresh()
            busy = reader.step()
            if time.time() >= next_report:
                comm.send(reader.take(), dest=0, tag=TAG_DATA)
                next_report = time.time() + settings.s["display"]["update_s"]
            if not busy:
                time.sleep(0.003)
        return

    # ----------------------------------------------------------- display (rank 0)
    label = "TEST data" if args.test else "olpy (live)"
    if replay is not None:
        label = "run {} replayed in a loop (stpy, {:g} shots/s)".format(replay, float(args.rate))
    if size > 1:
        label += ", {} MPI readers".format(size - 1)
    infos = source.infos()
    print("opening the window (geometry, matplotlib) ...", flush=True)
    viewer = Viewer(settings, infos, det_ids, label, settings.cfg.output_dir())
    local = Reader(source, settings) if size == 1 else None
    print("MPCCD viewer: {} panels, {}. Press h in the window for the keys.".format(len(det_ids), label))
    from silas.core import plotting

    state = {"next_draw": 0.0}

    def tick() -> bool:
        """One round: settings, read (or collect from the readers), draw when due."""
        if settings.refresh():
            viewer.settings_changed()
        update_s = float(settings.s["display"]["update_s"])
        if local is not None:  # one process: read until it is time to draw
            t_end = time.time() + 0.8 * update_s
            while time.time() < t_end:
                if not local.step():
                    time.sleep(0.003)
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
        detached = plotting.run_loop(viewer.plt, tick, 0.05, on_stop=stop)   # a window, the browser or a notebook
    except KeyboardInterrupt:
        pass
    finally:
        if not detached:
            stop()


def run_offline(args: Any, settings: Settings, runs: List[int]) -> None:
    """Offline view of stored runs: all shots loaded once, the average shown (run_cache)."""
    from silas.core import plotting
    from silas.offline.run_cache import OfflineRuns

    data = OfflineRuns(settings.cfg, settings.s, runs, max_tags=args.max_tags, every=args.every,
                       workers=args.workers, where=args.cache)
    holder = {}  # type: Dict[str, Any]

    def stop() -> None:
        if "viewer" in holder:
            plotting.close_all(holder["viewer"].plt, [holder["viewer"].fig])
        data.close()
        print("viewer stopped")

    detached = False      # True: running on in a notebook, stop() is called when it ends
    try:
        label = "stored run{} {} (offline: average of all shots)".format(
            "s" if len(runs) > 1 else "", ", ".join(str(r) for r in runs))
        print("opening the window (geometry, matplotlib) ...", flush=True)
        viewer = Viewer(settings, data.infos(), list(settings.s["panels"]), label, settings.cfg.output_dir())
        holder["viewer"] = viewer
        viewer.static = data
        viewer.run = runs[0]
        viewer.draw()            # starts the first computation
        print("MPCCD viewer (offline): {} panels, {}. Press h in the window for the keys, w for "
              "pump on / off.".format(len(viewer.det_ids), label))

        def tick() -> bool:
            """Redraw only when something changed: the settings, or a computation finished."""
            changed = settings.refresh()
            if changed:
                viewer.settings_changed()
            if data.poll() or changed:
                viewer.draw()
                if data.res is not None and not data.busy():
                    print("averages ready ({:.1f} s)".format(data.res["seconds"]), flush=True)
            return bool(viewer.running)

        try:
            detached = plotting.run_loop(viewer.plt, tick, 0.1, on_stop=stop)
        except KeyboardInterrupt:
            pass
    finally:
        if not detached:
            stop()


@sacla.requires_sacla
def run_live(args: Any) -> None:
    run_viewer(args)


def sort_options(args: Any) -> None:
    """Options of the other kind of viewer are ignored, with a message; defaults filled in."""
    offline = bool(args.run) and not args.replay
    ignored = []  # type: List[str]
    if args.run and args.test:
        ignored.append("test (stored runs are read with stpy)")
        args.test = False
    if offline:
        if args.rate is not None:
            ignored.append("rate (online replay only)")
    else:
        ignored += ["{} (offline only)".format(n) for n in ("max_tags", "every", "cache")
                    if getattr(args, n) is not None]
        if args.replay and not args.run:
            ignored.append("replay (needs run=)")
    if ignored:
        print("{} viewer: ignored {}".format("offline" if offline else "online", ", ".join(ignored)))
    args.rate = 10.0 if args.rate is None else args.rate
    args.every = 1 if args.every is None else args.every
    args.cache = args.cache or "auto"


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="MPCCD array viewer: live (online) or of stored runs (offline)")
    ap.add_argument("--array", help="array name or alias (default: the first MPCCD array in the config)")
    ap.add_argument("--test", action="store_true", help="replay test data instead of reading the detector")
    ap.add_argument("--test-data", help="test data file (default: output/diag/mpccd_panels.npz)")
    ap.add_argument("--run", type=int, nargs="+",
                    help="STORED run(s) (HPC, stpy): offline view, the average of all their shots")
    ap.add_argument("--replay", action="store_true", help="play the (first) run as if live instead, in a loop")
    ap.add_argument("--workers", type=int, default=6,
                    help="offline: processes loading / averaging (default 6; online the readers are mpirun's "
                         "processes)")
    ap.add_argument("--rate", type=float, help="online replay: shots per second (default 10)")
    ap.add_argument("--max-tags", type=int, help="offline: at most this many tags per run")
    ap.add_argument("--every", type=int, help="offline: every N-th tag only (default 1)")
    ap.add_argument("--cache", choices=("auto", "ram", "work"),
                    help="offline: where the loaded shots are kept (default auto: RAM if they fit)")
    args = ap.parse_args(argv)
    sort_options(args)
    try:
        if args.test:
            run_viewer(args)  # no SACLA library involved
        else:
            run_live(args)
    except KeyboardInterrupt:   # e.g. Ctrl-C while the offline view is still loading
        print("stopped (Ctrl-C)")


if __name__ == "__main__":
    main()
