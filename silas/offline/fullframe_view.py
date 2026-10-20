"""Viewer of a full-frame reduction: pump on, pump off and on / off of one step (or time-tool bin).

    view_full_frame(1234501)                         # session (HPC: in the browser; laptop: a window)
    view_full_frame("~/data/fullframe_1234501.h5")   # a copy at home
    python -m silas.offline.fullframe_view fullframe_1234501.h5 --step 10 --ref i0_1

Three images, assembled in the detector geometry (silas.offline.fullframe.FullFrame):

    pump on            the statistic over the step's pump-on shots
    pump off           ... over its pump-off shots (same colour scale as pump on)
    on / off           pump on / pump off of that statistic (median: the ratio of the medians, as
                       the ROI analysis); seismic colour scale centred on 1; key d: on - off
                       instead (centred on 0)

The three images zoom and pan together. The colour scales are fixed for the whole scan (they do not
change from step to step): pump on and pump off share one scale over all steps (or bins) of both;
on / off (on - off) has its own, with the centre 1 (0) in the middle of the colour map (two
slopes). Each range is 0.1-99.9 % of the pixels of all steps (key c: min-max instead; single hot
pixels stretch min-max). They are computed again when the reference, the statistic, steps / bins
or on/off <-> on-off change.
The reference starts at the first one of the reduction (--ref none: no reference).
In time-tool bins the time is shown from the left-most bin with images (t = 0 there).

Keys (in the window, or the browser page on the HPC):

    left / right       previous / next step (or bin);  up / down: 10 steps
    r                  reference: none -> each reference -> none (pixel / R per shot)
    m                  statistic: median <-> mean (the mean is what to use for weak signals)
    d                  third image: on / off <-> on - off
    b                  scan steps <-> time-tool bins (if the reduction has bins; only bins with images)
    l                  log colour scale of pump on / off
    k                  colour map of pump on / off: viridis -> magma -> inferno -> cividis -> gray -> ...
    c                  colour scales: 0.1-99.9 % of the pixels of all steps <-> min-max of all steps
    a                  aspect: real (square pixels) <-> fill the space
    s                  save the figure as png next to the h5 file
    q                  close

In time-tool bins: pump on is the bin (time-tool corrected), on / off is the stored step-wise
ratio (every shot / the median pump-off image of its step; on - off: every shot - that image),
and pump off is the pump-off image
of the step most of the bin's shots come from (pump-off shots are not binned).
"""
import argparse
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.offline.fullframe import FullFrame

CMAPS = ("viridis", "magma", "inferno", "cividis", "gray")
ROBUST = (0.1, 99.9)        # key c: percentiles of all steps' pixels instead of min-max
SAMPLE = 7                  # every 7th pixel of every step for those percentiles


class FullFrameView(object):
    """The three images of one step / bin and the keys (see the module text)."""

    def __init__(self, ff: FullFrame, step: int = 0, ref: Optional[str] = None, stat: str = "median",
                 binned: bool = False) -> None:
        self.ff = ff
        self.refs = [None] + list(ff.labels)  # type: List[Optional[str]]
        if ref is not None and ref not in ff.labels:
            raise ValueError("no reference {!r} in this reduction (it has: {})".format(ref, ", ".join(ff.labels) or "none"))
        self.ref = ref
        self.stat = stat
        self.two = len(ff.states) == 2
        self.bin_rows = []  # type: List[int]
        if ff.bins is not None and "computed" in ff.bins:
            self.bin_rows = [i for i, c in enumerate(ff.bins["computed"].to_numpy()) if c]
        self.binned = bool(binned and self.bin_rows)
        self.index = 0
        self.log = False
        self.diff = False                   # third image: on / off (False) or on - off (True)
        self.wide = 1                       # colour scales: 1 percentiles (ROBUST, default), 0 min-max
        self.fill = False                   # aspect: False real (square pixels), True fill the space
        self.cmap = 0                       # index into CMAPS (pump on / off)
        self._ranges = {}  # type: Dict[Tuple[Any, ...], Dict[str, Tuple[float, float]]]
        if self.binned:
            self.index = min(max(0, int(step)), len(self.bin_rows) - 1)
        else:
            self.index = min(max(0, int(step)), len(ff.steps) - 1)

    # ------------------------------------------------------------ data
    def kind(self) -> str:
        return "raw" if self.ref is None else self.ref

    def count(self) -> int:
        return len(self.bin_rows) if self.binned else len(self.ff.steps)

    def _main_step(self, row: int) -> int:
        """The step most shots of bin table row ``row`` come from."""
        sh = self.ff.shots
        b = int(self.ff.bins["bin"].iloc[row])
        if "bin" not in sh or "step_index" not in sh:
            return 0
        steps = sh.loc[sh["bin"].to_numpy() == b, "step_index"].to_numpy()
        steps = steps[steps >= 0]
        return int(np.bincount(steps).argmax()) if len(steps) else 0

    def t0_ps(self) -> float:
        """Time of the left-most bin with images: bins are shown from there (t = 0)."""
        if not self.bin_rows:
            return 0.0
        return float(self.ff.bins["t_ps"].iloc[self.bin_rows[0]])

    def arrays(self, index: int, off_cache: Optional[Dict[int, Any]] = None
               ) -> Tuple[np.ndarray, Optional[np.ndarray], Optional[np.ndarray]]:
        """(on, off, on / off or on - off) of step / bin ``index`` as (panels, rows, cols)."""
        ff, kind, st = self.ff, self.kind(), self.stat
        if self.binned:
            row = self.bin_rows[index]
            on = ff.image(kind, bin=row, stat=st)
            off = None  # type: Optional[np.ndarray]
            ratio = None  # type: Optional[np.ndarray]
            if self.two:
                j = self._main_step(row)
                if off_cache is not None and j in off_cache:
                    off = off_cache[j]
                else:
                    off = ff.image(kind, step=j, state="off", stat=st)
                    if off_cache is not None:
                        off_cache[j] = off
                stored = ("diff" if self.diff else "onoff") + ("" if self.ref is None else "_" + self.ref)
                ratio = ff.image(stored, bin=row, stat=st)
            return on, off, ratio
        on = ff.image(kind, step=index, state="on" if self.two else "all", stat=st)
        off = ff.image(kind, step=index, state="off", stat=st) if self.two else None
        ratio = None
        if off is not None:
            with np.errstate(invalid="ignore", divide="ignore"):
                # median: the ratio / difference of the medians (= FullFrame.onoff / .difference)
                ratio = on - off if self.diff else on / off
        return on, off, ratio

    def headline(self) -> str:
        """The step / bin and its time, shown in bold."""
        if self.binned:
            b = self.ff.bins.iloc[self.bin_rows[self.index]]
            return "bin {} of {}:   t = {:+.3f} ps".format(self.index + 1, self.count(), float(b["t_ps"]) - self.t0_ps())
        s = self.ff.steps.iloc[self.index]
        return "step {} of {}:   x = {:g}".format(self.index + 1, self.count(), float(s["x"]))

    def images(self) -> Tuple[np.ndarray, Optional[np.ndarray], Optional[np.ndarray], str]:
        """(on, off, on / off or on - off) as (panels, rows, cols) and the title of the current view."""
        ff, st = self.ff, self.stat
        on, off, ratio = self.arrays(self.index)
        ref_txt = "no reference" if self.ref is None else "per {} (per shot)".format(self.ref)
        if self.binned:
            row = self.bin_rows[self.index]
            b = ff.bins.iloc[row]
            j = self._main_step(row)
            n_ref = "" if self.ref is None or ("n_" + self.ref) not in b else ", {} with {}".format(
                int(b["n_" + self.ref]), self.ref)
            title = ("time-tool bin {} (t = {:.3f} ps absolute; t = 0: left-most bin), {} pump-on shots{}; "
                     "pump off: step {} (x = {:g})".format(int(b["bin"]), float(b["t_ps"]), int(b["n"]), n_ref,
                                                          j + 1, float(ff.steps["x"].iloc[j])))
        else:
            s = ff.steps.iloc[self.index]
            names = ("on", "off") if self.two else ("all",)
            suffix = "" if self.ref is None else "_" + self.ref
            counts = ", ".join("{} {}".format(int(s["n_" + n + suffix]), n) for n in names if "n_" + n + suffix in s)
            title = "runs {}, shots: {}".format(s["runs"], counts)
        title += "  |  {}, {}".format(st, ref_txt)
        return on, off, ratio, title

    def ranges(self) -> Dict[str, Tuple[float, float]]:
        """Colour scales over ALL steps (or bins) of the current settings, computed once per setting:
        "onoff" (pump on and off together) and "ratio" (on / off or on - off), each min-max or
        (key c) percentiles; "pos": the smallest value > 0 of on / off (log scale)."""
        key = (self.kind(), self.stat, self.binned, self.diff, self.wide)
        if key in self._ranges:
            return self._ranges[key]
        n = self.count()
        print("colour scales over all {} {} ...".format(n, "bins" if self.binned else "steps"), flush=True)
        lo = [np.inf, np.inf]
        hi = [-np.inf, -np.inf]
        pos = np.inf
        samples = ([], [])  # type: Tuple[List[np.ndarray], List[np.ndarray]]
        off_cache = {}  # type: Dict[int, Any]
        for i in range(n):
            on, off, ratio = self.arrays(i, off_cache)
            groups = ([on] + ([off] if off is not None else []), [ratio] if ratio is not None else [])
            for g, arrays in enumerate(groups):
                for a in arrays:
                    v = a[np.isfinite(a)]
                    if not v.size:
                        continue
                    lo[g], hi[g] = min(lo[g], float(v.min())), max(hi[g], float(v.max()))
                    if g == 0:
                        p = v[v > 0]
                        if p.size:
                            pos = min(pos, float(p.min()))
                    if self.wide:
                        samples[g].append(v[::SAMPLE])
        out = {}  # type: Dict[str, Tuple[float, float]]
        for g, name in enumerate(("onoff", "ratio")):
            if self.wide and samples[g]:
                q = np.percentile(np.concatenate(samples[g]), ROBUST)
                out[name] = (float(q[0]), float(q[1]))
            elif np.isfinite(lo[g]):
                out[name] = (lo[g], hi[g])
            else:
                out[name] = (0.0, 1.0)
        out["pos"] = (pos if np.isfinite(pos) else 1e-6, 0.0)
        self._ranges[key] = out
        return out

    # ------------------------------------------------------------ keys
    def key(self, k: str) -> bool:
        """Change the view for key ``k``; True if something changed."""
        n = self.count()
        if k in ("right", "left", "up", "down"):
            d = {"right": 1, "left": -1, "up": 10, "down": -10}[k]
            new = min(max(0, self.index + d), n - 1)
            if new == self.index:
                return False
            self.index = new
        elif k == "r":
            self.ref = self.refs[(self.refs.index(self.ref) + 1) % len(self.refs)]
        elif k == "d":
            self.diff = not self.diff
        elif k == "m":
            self.stat = "mean" if self.stat == "median" else "median"
        elif k == "b":
            if not self.bin_rows:
                return False
            if self.binned:                 # back to the step most of the bin's shots are from
                self.index = self._main_step(self.bin_rows[self.index])
                self.binned = False
            else:
                self.binned = True
                self.index = 0
        elif k == "l":
            self.log = not self.log
        elif k == "c":
            self.wide = 1 - self.wide
        elif k == "a":
            self.fill = not self.fill
        elif k == "k":
            self.cmap = (self.cmap + 1) % len(CMAPS)
        else:
            return False
        return True


def _centred_norm(lo: float, hi: float, centre: float) -> Any:
    """Colour norm from lo to hi with ``centre`` in the middle of the colour map (two slopes):
    matplotlib's TwoSlopeNorm (DivergingNorm before matplotlib 3.2)."""
    import matplotlib.colors as mcolors

    cls = getattr(mcolors, "TwoSlopeNorm", None) or getattr(mcolors, "DivergingNorm")
    if not (np.isfinite(lo) and np.isfinite(hi)):
        lo, hi = centre, centre
    # the centre must lie strictly inside, also when every pixel is exactly at it (e.g. on / off = 1)
    gap = max(1e-6 * max(abs(hi - centre), abs(lo - centre)), 1e-6 * max(abs(centre), 1.0))
    lo = min(lo, centre - gap)
    hi = max(hi, centre + gap)
    return cls(vcenter=centre, vmin=lo, vmax=hi)


def show(path: str, step: int = 0, ref: Optional[str] = None, stat: str = "median", binned: bool = False,
         save: Optional[str] = None) -> None:
    """Open the viewer (save: only write that png, no window; for tests and batch jobs)."""
    import matplotlib

    from silas.core import plotting

    ff = FullFrame(os.path.expanduser(path))
    if ref is None and ff.labels:          # default: the first reference
        ref = ff.labels[0]
    elif ref is not None and ref.lower() == "none":
        ref = None
    view = FullFrameView(ff, step=step, ref=ref, stat=stat, binned=binned)
    plt = None  # type: Any
    if save:
        matplotlib.use("Agg")
        import matplotlib.pyplot as pyplot_agg

        plt = pyplot_agg
    else:
        plt = plotting.pyplot(interactive=True)
        for k in list(matplotlib.rcParams):          # our keys, not matplotlib's (l = log y, s = save, ...)
            if k.startswith("keymap.") and k != "keymap.quit":
                matplotlib.rcParams[k] = []
    from matplotlib.colors import LogNorm, Normalize

    n_img = 3 if view.two else 1
    # sharex / sharey: zoom and pan in one image does the same in the others
    fig, axes = plt.subplots(1, n_img, figsize=(5.4 * n_img, 6.4), squeeze=False, sharex=True, sharey=True)
    fig.subplots_adjust(left=0.04, right=0.97, bottom=0.06, top=0.80, wspace=0.25)
    headline = fig.text(0.5, 0.875, "", ha="center", va="center", fontsize=14, fontweight="bold")
    geom = ff.geometry()
    covered = geom.mask()                         # only the chosen panels: crop to them (+ a margin)
    rows, cols = np.nonzero(covered.any(axis=1))[0], np.nonzero(covered.any(axis=0))[0]
    m = 10
    crop = (slice(max(0, rows[0] - m), rows[-1] + m + 1), slice(max(0, cols[0] - m), cols[-1] + m + 1))
    ims = []  # type: List[Any]
    cbars = []  # type: List[Any]
    for ax in axes[0]:
        im = ax.imshow(np.zeros(geom.shape, np.float32)[crop], cmap="viridis", interpolation="nearest")
        ims.append(im)
        cbars.append(fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03))
        ax.set_xticks([])
        ax.set_yticks([])

    def draw() -> None:
        on, off, ratio, title = view.images()
        a_on = ff.assemble(on)[crop]
        parts = [a_on]
        if view.two and off is not None and ratio is not None:
            a_off = ff.assemble(off)[crop]
            a_ratio = ff.assemble(ratio)[crop]
            parts = [a_on, a_off, a_ratio]
        rng = view.ranges()
        lo, hi = rng["onoff"]
        how = "{}-{} % of all {}".format(ROBUST[0], ROBUST[1], "bins" if view.binned else "steps") if view.wide \
            else "min-max of all {}".format("bins" if view.binned else "steps")
        unit = "keV per pixel per shot" if view.ref is None else "keV per pixel per shot / {}".format(view.ref)
        names = ["pump on", "pump off", "on - off" if view.diff else "on / off"] if view.two \
            else ["all shots (no pump states)"]
        for i, (im, a) in enumerate(zip(ims, parts)):
            im.set_data(a)
            if i < 2:
                im.set_cmap(CMAPS[view.cmap])
                if view.log:
                    vmin = max(lo, rng["pos"][0])
                    im.set_norm(LogNorm(vmin=vmin, vmax=max(hi, vmin * 10)))
                else:
                    im.set_norm(Normalize(lo, hi if hi > lo else lo + 1e-6))
                axes[0, i].set_title("{}\n{} ({})".format(names[i], view.stat, unit), fontsize=9)
            else:
                r_lo, r_hi = rng["ratio"]
                im.set_cmap("seismic")
                im.set_norm(_centred_norm(r_lo, r_hi, 0.0 if view.diff else 1.0))
                op = "-" if view.diff else "/"
                what = "{} of (shot {} median off of its step)".format(view.stat, op) if view.binned \
                    else "{} on {} {} off".format(view.stat, op, view.stat)
                axes[0, i].set_title("{}\n{}{}".format(names[i], what, " ({})".format(unit) if view.diff else ""),
                                     fontsize=9)
            cbars[i].update_normal(im)
        keys = "keys: left/right step, up/down 10, r reference, m median/mean, d on/off or on-off, {}l log, " \
            "k colour map, c colour scale ({}), a aspect, s save".format("b steps/bins, " if view.bin_rows else "", how)
        fig.suptitle("{}  -  scan {}, panels {}\n{}\n{}".format(
            os.path.basename(path), ff.info["scan"].get("scan_id"), ff.info.get("panel_numbers"), title, keys),
            fontsize=9)
        headline.set_text(view.headline())
        for ax in axes[0]:
            ax.set_aspect("auto" if view.fill else "equal")
        fig.canvas.draw_idle()

    state = {"open": True}  # type: Dict[str, Any]

    def on_key(event: Any) -> None:
        if event.key == "s":
            name = os.path.splitext(path)[0] + "_view_{}{}_{}_{}{}.png".format(
                "bin" if view.binned else "step", view.index + 1, view.ref or "raw", view.stat,
                "_diff" if view.diff else "")
            fig.savefig(name, dpi=110)
            print("saved", name, flush=True)
            return
        if event.key == "q":
            state["open"] = False
            plt.close(fig)
            return
        if view.key(str(event.key)):
            draw()

    draw()
    if save:
        fig.savefig(save, dpi=80)
        plt.close(fig)
        ff.close()
        return
    fig.canvas.mpl_connect("key_press_event", on_key)
    fig.canvas.mpl_connect("close_event", lambda event: state.update(open=False))
    print("full-frame viewer: {} ({} steps{}); keys: see the window title".format(
        path, len(ff.steps), ", {} bins with images".format(len(view.bin_rows)) if view.bin_rows else ""), flush=True)

    def stop() -> None:
        plt.close(fig)
        ff.close()

    detached = False      # True: running on in a notebook, stop() is called when it ends
    try:
        detached = plotting.run_loop(plt, lambda: bool(state["open"]), 0.2, on_stop=stop)
    except KeyboardInterrupt:
        pass
    finally:
        if not detached:
            stop()


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="viewer of a full-frame reduction: pump on, off and on / off")
    ap.add_argument("path", help="the fullframe_<id>[_p...].h5 file")
    ap.add_argument("--step", type=int, default=1, help="step (or bin) to start with, 1 = the first")
    ap.add_argument("--ref", help="start with this reference (default: the first one; none: no reference)")
    ap.add_argument("--stat", choices=("median", "mean"), default="median")
    ap.add_argument("--bins", action="store_true", help="start with the time-tool bins")
    ap.add_argument("--save", help="only write the view to this png (no window)")
    a = ap.parse_args(argv)
    show(a.path, step=a.step - 1, ref=a.ref, stat=a.stat, binned=a.bins, save=a.save)


if __name__ == "__main__":
    main()
