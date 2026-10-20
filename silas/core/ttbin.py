"""Time-tool binning of a scan: the shots sorted into bins of their corrected timing.

Every pump-on shot with a good edge gets its timing (silas.core.timetool):

    t = <scan motor value of its run> + P(edge) - P(0)            (pulse)

and goes into a bin of width ``bin_ps`` (ps, converted with timetool's ps_per_pulse). The grid
is anchored on the FIRST such shot of the scan (in run and tag order): it is the centre of bin 0,
and a shot falls into bin k = round((t - t0) / width). Bins are thus added in either direction
as shots need them; offline and online (where the bins grow as the scan goes on) give the same
bins for the same shots.

Pump on / off stays step-wise, as without binning: every pump-on shot is divided by the MEDIAN
of the pump-off shots of its scan step (repeated steps combined), and these per-shot ratios are
binned. The pump-off shots themselves are not binned (their timing means nothing).

Shots without a good edge (or without a time-tool log) are left out. Bins with fewer than
``sparse_min`` shots are "sparse": kept in the tables, hidden in the plots when ``sparse_hide``
is on (keys in the plot window change both; they are stored in the config).

Config (section "scan"): tt_binning (false), tt_bin_ps (0.05), sparse_hide (true),
sparse_min (20).
"""
import math
import warnings
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from silas.core import scanroi, timetool

KEYS = {"tt_binning": False, "tt_bin_ps": 0.05, "sparse_hide": True, "sparse_min": 20}


def settings(cfg: Any) -> Dict[str, Any]:
    return {k: cfg.get("scan." + k, v) for k, v in KEYS.items()}


def set_option(cfg: Any, key: str, value: Any) -> None:
    if key not in KEYS:
        raise KeyError("unknown binning setting {!r} ({})".format(key, ", ".join(KEYS)))
    if key == "tt_bin_ps" and not float(value) > 0:
        raise ValueError("the bin width must be > 0 ps")
    if key == "sparse_min":
        value = max(1, int(value))
    cfg.set("scan." + key, value)


def describe(cfg: Any) -> str:
    b = settings(cfg)
    ps = float(timetool.settings(cfg).get("ps_per_pulse", 0.006671))
    return ("time-tool binning {}: bins of {:g} ps = {:.4g} pulse; sparse bins (< {} shots) {}".format(
        "ON" if b["tt_binning"] else "off", float(b["tt_bin_ps"]), float(b["tt_bin_ps"]) / ps, int(b["sparse_min"]),
        "hidden in the plots" if b["sparse_hide"] else "shown"))


class BinGrid(object):
    """Bins of width ``width`` anchored on the first value given (the centre of bin 0); grows."""

    def __init__(self, width: float, t0: Optional[float] = None) -> None:
        self.width = float(width)
        self.t0 = t0
        self.kmin = None  # type: Optional[int]
        self.kmax = None  # type: Optional[int]

    def index(self, t: Any) -> np.ndarray:
        """Bin index of every value (NaN values: a very negative number, never a bin)."""
        t = np.atleast_1d(np.asarray(t, np.float64))
        ok = np.isfinite(t)
        if self.t0 is None and ok.any():
            self.t0 = float(t[ok][0])
        if self.t0 is None:
            return np.full(len(t), np.iinfo(np.int64).min)
        k = np.full(len(t), np.iinfo(np.int64).min, np.int64)
        k[ok] = np.floor((t[ok] - self.t0) / self.width + 0.5).astype(np.int64)
        if ok.any():
            lo, hi = int(k[ok].min()), int(k[ok].max())
            self.kmin = lo if self.kmin is None else min(self.kmin, lo)
            self.kmax = hi if self.kmax is None else max(self.kmax, hi)
        return k

    def centre(self, k: Any) -> np.ndarray:
        return float(self.t0 or 0.0) + np.asarray(k, np.float64) * self.width

    def bins(self) -> np.ndarray:
        if self.kmin is None or self.kmax is None:
            return np.zeros(0, np.int64)
        return np.arange(self.kmin, self.kmax + 1)


# ====================================================================== offline
def attach_timing(cfg: Any, shots: Any, motor: Optional[str]) -> Dict[str, Any]:
    """Add to the shot table: edge, edge_good, shift_pulse (time-tool log, current calibration),
    t_pulse = value + shift_pulse, t_ps. Returns notes {"missing_runs", "unit_warning", ...}."""
    import pandas as pd

    from silas.core import names

    s = timetool.settings(cfg)
    cal = s.get("calibration")
    notes = {"missing_runs": [], "unit_warning": "", "no_calibration": not cal}  # type: Dict[str, Any]
    if motor:
        mu = str(names.entry(cfg, motor).get("unit") or "")
        cu = str((cal or {}).get("unit") or "")
        if mu and cu and mu.lower() != cu.lower():
            notes["unit_warning"] = ("WARNING: the scanned motor {} is in {}, the time-tool calibration in {}: "
                                     "the timing adds the two as they are".format(motor, mu, cu))
    parts = []
    for run in sorted(set(int(r) for r in shots["run"])):
        df = timetool.load_edges(cfg, run)
        if not len(df):
            notes["missing_runs"].append(run)
            continue
        parts.append(pd.DataFrame({"run": run, "tag": df["tag"].astype(np.int64), "edge": df["edge"],
                                   "edge_good": df["good"].astype(bool), "shift_pulse": df["shift_pulse"]}))
    for c in ("edge", "edge_good", "shift_pulse", "t_pulse", "t_ps"):
        if c in shots:
            del shots[c]
    if parts:
        tt = pd.concat(parts, ignore_index=True)
        merged = shots[["run", "tag"]].merge(tt, on=["run", "tag"], how="left")
        for c in ("edge", "edge_good", "shift_pulse"):
            shots[c] = merged[c].to_numpy()
    else:
        shots["edge"], shots["edge_good"], shots["shift_pulse"] = np.nan, False, np.nan
    shots["edge_good"] = shots["edge_good"].fillna(False).astype(bool)
    value = pd.to_numeric(shots["value"], errors="coerce").to_numpy(np.float64)
    shots["t_pulse"] = value + shots["shift_pulse"].to_numpy(np.float64)
    shots["t_ps"] = shots["t_pulse"] * float(s.get("ps_per_pulse", 0.006671))
    return notes


def stepwise_onoff(shots: Any, cols: Sequence[str], runs: Sequence[Dict[str, Any]]) -> List[str]:
    """For every pump-on shot and column c: c / median(c of the pump-off shots of its scan step,
    repeated steps combined) -> new column c + "_onoff" (NaN for pump-off shots). Returns the new
    columns."""
    template = scanroi.step_table(scanroi.ScanSums(1, 2), runs)
    out = []  # type: List[str]
    for c in cols:
        new = c + "_onoff"
        vals = np.full(len(shots), np.nan)
        for st in template:
            in_step = shots["run"].isin(st["runs"]).to_numpy()
            off = shots.loc[in_step & (shots["state"].to_numpy() == 1), c].to_numpy(np.float64)
            off = off[np.isfinite(off)]
            if not off.size:
                continue
            m = float(np.median(off))
            on = in_step & (shots["state"].to_numpy() == 0)
            with np.errstate(invalid="ignore", divide="ignore"):
                vals[on] = shots.loc[on, c].to_numpy(np.float64) / m
        shots[new] = vals
        out.append(new)
    return out


def bin_table(shots: Any, cols: Sequence[str], width_pulse: float, ps_per_pulse: float,
              q: Sequence[float] = scanroi.QUANTILES) -> Tuple[Any, BinGrid]:
    """Per bin of the pump-on shots with a timing: n and the quantiles of every column.
    Returns (DataFrame with bin, t_pulse, t_ps, n, <c>_q16 / _median / _q84 ...; the shots get
    a column "bin"), grid."""
    import pandas as pd

    grid = BinGrid(width_pulse)
    use = shots["read"].to_numpy(bool) & (shots["state"].to_numpy() == 0) & shots["edge_good"].to_numpy(bool) \
        & np.isfinite(shots["t_pulse"].to_numpy(np.float64))
    order = np.lexsort((shots["tag"].to_numpy(), shots["run"].to_numpy()))
    first = [i for i in order if use[i]]
    if first:
        grid.t0 = float(shots["t_pulse"].to_numpy(np.float64)[first[0]])
    k = np.full(len(shots), np.iinfo(np.int64).min, np.int64)
    if use.any():
        k[use] = grid.index(shots["t_pulse"].to_numpy(np.float64)[use])
    shots["bin"] = np.where(use, k, -10 ** 9)
    rows = []  # type: List[Dict[str, Any]]
    for b in grid.bins():
        part = shots.loc[use & (k == b)]
        c = float(grid.centre(b))
        row = {"bin": int(b), "t_pulse": c, "t_ps": c * ps_per_pulse, "n": int(len(part))}  # type: Dict[str, Any]
        if len(part):
            row["t_ps_median"] = float(np.median(part["t_ps"].to_numpy(np.float64)))
        for col in cols:
            v = part[col].to_numpy(np.float64) if len(part) else np.zeros(0)
            v = v[np.isfinite(v)]
            if v.size:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    qa = np.quantile(v, list(q))
                row.update({col + "_q16": qa[0], col + "_median": qa[1], col + "_q84": qa[2]})
            else:
                row.update({col + "_q16": math.nan, col + "_median": math.nan, col + "_q84": math.nan})
        rows.append(row)
    return pd.DataFrame(rows), grid


def off_steps(shots: Any, cols: Sequence[str], runs: Sequence[Dict[str, Any]], ps_per_pulse: float,
              q: Sequence[float] = scanroi.QUANTILES) -> Any:
    """The pump-off shots per scan step (repeated steps combined), NOT time-tool corrected: their
    timing means nothing, they are drawn at the step's motor value. One row per step: step,
    value (pulse), t_ps (= value x ps_per_pulse), n, <c>_q16 / _median / _q84."""
    import pandas as pd

    rows = []  # type: List[Dict[str, Any]]
    for st in scanroi.step_table(scanroi.ScanSums(1, 2), runs):
        part = shots[shots["run"].isin(st["runs"]) & (shots["state"] == 1) & shots["read"].astype(bool)]
        v = st.get("value")
        value = float(v) if v is not None and v == v else math.nan
        row = {"step": st.get("step"), "value": value, "t_ps": value * ps_per_pulse,
               "n": int(len(part))}  # type: Dict[str, Any]
        for col in cols:
            x = part[col].to_numpy(np.float64) if len(part) and col in part else np.zeros(0)
            x = x[np.isfinite(x)]
            qa = np.quantile(x, list(q)) if x.size else [math.nan] * 3
            row.update({col + "_q16": qa[0], col + "_median": qa[1], col + "_q84": qa[2]})
        rows.append(row)
    return pd.DataFrame(rows)


# ====================================================================== plot
def plot_bins(cfg: Any, bins: Any, panels: List[Tuple[str, List[str], str]], title: str, path: Optional[str],
              show: bool = False, off: Any = None) -> None:
    """One column of panels against the timing (ps), one marker per ROI, error bars 16-84 %; a
    panel of the shots per bin with the sparse limit. panels: [(y label, [column names], kind)],
    kind "roi" or "onoff" (a line at 1). off: the pump-off shots per step (off_steps), drawn
    dashed with open markers at the step's motor value in ps in the "roi" panels. The time axis
    starts at 0 at the left-most bin drawn; the pump-off points are shifted on their own so the
    left-most step is also at 0. Shown: keys + / - change the sparse limit (x 1.25),
    h hides / shows sparse bins; both are stored in the config, and the png is saved again."""
    import matplotlib

    from silas.core import plotting

    plt = None  # type: Any
    if show:
        try:
            plt = plotting.pyplot()
        except RuntimeError as err:
            print("plot window not possible ({}); the plot is only saved".format(err))
            show = False
    if not show:
        matplotlib.use("Agg")
        import matplotlib.pyplot as pyplot_agg

        plt = pyplot_agg
    if show:
        for k in list(matplotlib.rcParams):
            if k.startswith("keymap."):
                matplotlib.rcParams[k] = []
    n_rows = len(panels) + 1
    fig, axes = plt.subplots(n_rows, 1, figsize=(12, 2.8 * len(panels) + 2.4), sharex=True, squeeze=False,
                             gridspec_kw={"height_ratios": [3.0] * len(panels) + [1.4]})
    fig.subplots_adjust(left=0.1, right=0.76, hspace=0.12, top=0.94)

    def draw() -> None:
        b = settings(cfg)
        minimum, hide = int(b["sparse_min"]), bool(b["sparse_hide"])
        x_abs = bins["t_ps"].to_numpy(np.float64)
        n = bins["n"].to_numpy(np.int64)
        dense = n >= minimum
        shown = dense if hide else np.ones(len(n), bool)
        cands = [v for v in x_abs[shown & (n > 0)] if np.isfinite(v)]
        zero = float(min(cands)) if cands else 0.0
        xo_abs = off["t_ps"].to_numpy(np.float64) if off is not None and len(off) else np.zeros(0)
        cands_off = [v for v in xo_abs if np.isfinite(v)]
        zero_off = float(min(cands_off)) if cands_off else 0.0
        x = x_abs - zero
        xo = xo_abs - zero_off
        for ax in axes[:, 0]:
            ax.cla()
        for row, (label, cols, kind) in enumerate(panels):
            ax = axes[row, 0]
            for i, col in enumerate(cols):
                if col + "_median" not in bins:
                    continue
                med = bins[col + "_median"].to_numpy(np.float64)
                lo = med - bins[col + "_q16"].to_numpy(np.float64)
                hi = bins[col + "_q84"].to_numpy(np.float64) - med
                c = "C{}".format(i % 10)
                name = col.replace("roi_", "").replace("_onoff", "")
                ax.errorbar(x[dense], med[dense], yerr=[lo[dense], hi[dense]], color=c, marker="o", ms=4, lw=1.0,
                            capsize=2, label=name)
                if not hide and (~dense).any():
                    ax.errorbar(x[~dense], med[~dense], yerr=[lo[~dense], hi[~dense]], color=c, marker="o",
                                mfc="white", ms=4, ls="none", lw=0.8, capsize=2, alpha=0.6)
                if kind == "roi" and off is not None and col + "_median" in off and len(off):
                    m2 = off[col + "_median"].to_numpy(np.float64)
                    l2 = m2 - off[col + "_q16"].to_numpy(np.float64)
                    h2 = off[col + "_q84"].to_numpy(np.float64) - m2
                    ax.errorbar(xo, m2, yerr=[l2, h2], color=c, marker="s", mfc="white", ms=4, ls="--", lw=0.9,
                                capsize=2, label=name + " off (per step)")
            if kind == "onoff":
                ax.axhline(1.0, color="0.5", lw=0.8)
            ax.set_ylabel(label)
            ax.grid(alpha=0.3)
            ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.01, 1.0))
        ax = axes[-1, 0]
        width = float(bins["t_ps"].diff().median()) if len(bins) > 1 else 0.01
        ax.bar(x, n, width=0.9 * width, color=np.where(dense, "C0", "0.7"))
        ax.axhline(minimum, color="C3", lw=1.0, ls="--")
        ax.set_ylabel("shots per bin")
        off_txt = "; pump off = scan motor x ps_per_pulse, 0 = its left-most step (at {:.3f} ps)".format(
            zero_off) if len(cands_off) else ""
        ax.set_xlabel("time (ps): pump on = (scan motor + time-tool correction) x ps_per_pulse, 0 = left-most bin "
                      "(at {:.3f} ps){}".format(zero, off_txt), fontsize=9)
        ax.grid(alpha=0.3)
        fig.suptitle("{}\nsparse: < {} shots ({}); {} of {} bins{}".format(
            title, minimum, "hidden" if hide else "open markers", int((~dense).sum()), len(n),
            "   keys: + / - sparse limit, h hide / show sparse bins" if show else ""), fontsize=10)
        if path:
            fig.savefig(path, dpi=110)
        fig.canvas.draw_idle()

    def on_key(event: Any) -> None:
        b = settings(cfg)
        if event.key in ("+", "="):
            set_option(cfg, "sparse_min", int(math.ceil(int(b["sparse_min"]) * 1.25)))
        elif event.key == "-":
            set_option(cfg, "sparse_min", int(int(b["sparse_min"]) / 1.25))
        elif event.key == "h":
            set_option(cfg, "sparse_hide", not bool(b["sparse_hide"]))
        else:
            return
        b = settings(cfg)
        print("sparse bins: fewer than {} shots, {} (stored in the config)".format(
            int(b["sparse_min"]), "hidden" if b["sparse_hide"] else "shown"), flush=True)
        draw()

    draw()
    if show:
        fig.canvas.mpl_connect("key_press_event", on_key)
        plotting.show(plt, fig)
        plotting.close(plt, fig)  # (a notebook widget stays open: zoom, pan, keys)
    else:
        plt.close(fig)
