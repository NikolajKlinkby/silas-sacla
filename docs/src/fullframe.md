# Full-frame reduction

The [scan analysis](scans.md) follows a few regions of the detector (ROIs) through a scan.
The **full-frame reduction** does the same analysis for **every pixel** of the chosen panels
and saves the results as images: one image per scan step and pump state (and per time-tool
bin), unreferenced and referenced shot by shot, together with the values of the point detectors
for every shot. The point is to take the images home and look for signal anywhere on the
detector, not only where the ROIs were.

It reads every shot of the scan and computes statistics over millions of pixels, so it runs
on the HPC as a **batch job** (minutes for two panels, longer for all eight panels of an octal),
and its file is large (GB). For a quick test it can also run in the session.

```python
set_ff_panels(7, 8)                             # which panels (1-based, as in the viewer); () = all
reduce_full_frame(1234501, max_tags=100)        # quick test in this terminal (bt_node)
full_frame_job(1234501)                         # the whole scan as a batch job (qsub)
full_frame_job(1234501, tt=True, pairs=True)    # with time-tool bins and nearest-off pairs

ff = load_full_frame(1234501)                   # the result (or FullFrame("<file>.h5") at home)
img = ff.image("raw", step=10, state="on")      # median image (panels, rows, cols), keV per pixel per shot
ratio, lo, hi = ff.onoff("i0_1", step=10)       # pump on / off of the medians, per i0 referenced
diff, lo, hi = ff.difference("i0_1", step=10)   # pump on - off of the medians (stat="mean": of the means)
plt.imshow(ff.assemble(ratio))                  # the panels in their detector positions
ff.points["i0_2"]                               # a point detector, every shot (the rows of ff.shots)
```

## Before a reduction

Everything the ROI analysis uses is used here, from the same settings: dark, photon energy and
threshold ([Energy calibration, threshold and dark](energy-calibration.md)), the pump detector
(`set_pump`), the references (`set_references`), the time-tool settings and calibration and
`set_tt_bins` ([Time tool](timetool.md), [Time-tool binning](scans.md#time-tool-binning)), and the
run log ([Run logger](run-logger.md)). The ROIs themselves are **not** needed.

**Panels.** Only the chosen panels are read and stored. Choosing them saves time and space:
the octal MPCCD has 8 panels of 512 × 1024 pixels, often only some of them see the signal.

```python
set_ff_panels(7, 8)            # stored with the MPCCD array (use_mpccd), used by every reduction
set_ff_panels()                # all panels (the default)
full_frame_job(1234501, panels=[1, 2, 3])   # another choice for one reduction only
```

The panel numbers are the viewer's (1 = the array's first panel). The file name says which
panels it has: `fullframe_1234501.h5` (all), `fullframe_1234501_p78.h5` (panels 7 and 8).

## What is computed

The shots are those of the ROI analysis ([Which shots are used](scans.md#which-shots-are-used)):
X-rays off, bad beam and unknown pump state are left out, pump on / off from the pump detector,
and a shot without a value for a reference is left out of everything computed with that
reference. A shot that cannot be read (any chosen panel) is left out and counted
(`n_unreadable`).

**The value of a pixel in a shot**: (raw − dark), below the threshold → 0, times keV per ADU of
its panel: **keV per pixel per shot**, as for the ROIs (an ROI's value is the mean of these
over its pixels).

**Per scan step** (repeated steps combined) and pump state, over the shots, for every pixel:

| Statistic | |
|---|---|
| `q16`, `median`, `q84` | the 15.87 / 50 / 84.13 % quantiles, exactly as the ROI analysis (numpy's linear interpolation): the point and the spread of the shots |
| `mean` | the mean over the shots |

The **mean** is new. A single pixel sees few photons in a shot, mostly 0, 1 or 2, so its median
is often exactly 0 (or one photon's energy) where an ROI's median is a smooth number. For weak
signals use the mean: averaged over an ROI's pixels it is the ROI's mean (checked in
`tests/test_fullframe.py`). The quantiles are still there for strong signals (Bragg peaks)
and as the spread of the shots.

| Images | What | When |
|---|---|---|
| `raw` | the pixel values | always |
| per reference R | pixel / R of every shot | with references |
| `raw_with_<R>` | the pixel values of the shots with R only, for median pixel / median R per step | `step_reference=True` |
| `pairs`, `pairs_<R>` | every pump-on shot / the nearest pump-off shot (by tag) of its run, unreferenced and per R | `pairs=True` |
| `pairs_diff`, `pairs_diff_<R>` | every pump-on shot − the nearest pump-off shot, unreferenced and per R | `pairs=True` |

**Pump on / off and on − off** of a step are not stored as images: they are computed from the
stored on and off images when loading.

- `ff.onoff(kind, step)`: ratio of the medians, as the ROI analysis (relative errors added in
  quadrature).
- `ff.difference(kind, step)`: difference of the medians, the errors of the two added in
  quadrature (the lower error from the lower error of on and the upper error of off);
  `stat="mean"`: the difference of the means.
- The step reference: `ff.step_referenced(R, step)`. Anything else (e.g. the ratio of the means)
  is one line with `ff.image(...)`.

**Time-tool bins** (when time-tool binning is on: `set_tt_bins(on=True)` or `tt=True`): the same
bins as the ROI analysis (the same shots, grid and bin width). Per bin, pump on only:

| Images | What |
|---|---|
| `raw` | the pixel values of the bin's shots |
| per reference R | pixel / R |
| `onoff`, `onoff_<R>` | pump on / off step-wise: every shot / the **median pump-off image of its scan step** (unreferenced, or per R), then the statistics of these ratios |
| `diff`, `diff_<R>` | pump on − off step-wise: every shot − the median pump-off image of its step, then the statistics |

Pixels where a ratio is not a number (x / 0 in an empty pixel) are left out of its statistics,
as in the ROI binning. **Sparse bins**: bins with fewer than `min_shots` shots (default: the
`sparse_min` of `set_tt_bins`: 20, 30 in the BL3 standard config) are in the bin table but get **no images** (NaN): images of a
few shots are mostly noise and as large as any other. `min_shots=1` computes every bin. The
pump-off shots per step are the `off` images of the steps.

Without a pump detector there is one state, `all`, and no on / off.

**Point detectors.** Every point detector of the config is stored for **every shot** (read with
the shot flags, NaN where it was not recorded), except the X-ray shutter (`set_xfel_shutter`) and
the pump detector, whose values are the shot table's `use` and `state`, and the scanned motor,
whose value is the step's `x`. They are labelled by their alias (else the true name, with `/`
replaced by `_`, as the references). Per step and pump state, over the shots that are in the
images, the reduction also stores `n`, `mean`, `std` and the `q16` / `median` / `q84` quantiles
of each (`point_steps`). For time-tool bins or any other grouping, use the per-shot values with
the shot table (see [Point detectors](#point-detectors) below).

## Running it

### As a batch job (whole scans)

```python
full_frame_job(1234501)                             # submits: 16 cores, memory estimated
full_frame_job(1234501, panels=[7, 8], tt=True)     # options as reduce_full_frame
full_frame_job(1234501, cores=32, mem_gb=96)        # more of the node
full_frame_job(1234501, submit=False)               # only print the qsub command
```

`full_frame_job` builds the `qsub` command for `scripts/fullframe_job.sh` and submits it. If the
compute node of your session cannot submit jobs, it prints the command: paste it on the login
node. The job's memory is estimated from the largest step in the run log (see Memory below).
Its output goes to `~/SILAS/output/fullframe_job.log`:

```bash
qstat -u $USER                                      # is it running?
tail -f ~/SILAS/output/fullframe_job.log      # one line per step and bin
```

The job script can also be submitted by hand on the login node; its variables are listed at its
top:

```bash
qsub -v SCAN=1234501 ~/SILAS/scripts/fullframe_job.sh
qsub -v SCAN=1234501,PANELS=7:8,TT=1,PAIRS=1 ~/SILAS/scripts/fullframe_job.sh
qsub -l select=1:ncpus=16:mem=96gb -v SCAN=1234501 ~/SILAS/scripts/fullframe_job.sh
```

### In the session (tests)

```python
reduce_full_frame(1234501, max_tags=100)            # 100 shots per run: a few minutes
reduce_full_frame(1234501, max_tags=100, panels=[7], workers=16)
```

It runs in the session's terminal (Ctrl-C stops it). Use it on a `bt_node big` (16 cores,
64 GB): the statistics use every worker, unlike reading.

## Memory and time

One group of shots at a time (a scan step, later a time-tool bin):

1. **Reading**: the raw panels of the group's shots are read once (stpy, the workers share the
   shots) into a buffer: 16-bit integers when the stored values are whole-number ADU, else 32-bit
   floats (the job says which); in RAM (`/dev/shm`) if it fits in half the job's memory,
   otherwise on `/work` (slower). **1 or 2 MB per panel and shot**: a step of 1000 shots is 2-4 GB
   for 2 panels, 8-16 GB for 8.
2. **Statistics**: the workers share the pixels (blocks of rows of a panel); each computes
   everything of its block from the buffer. This needs the cores, not the storage.
3. **Writing**: the images of the group go into the file.

The job prints a line per step and bin with its time. The scratch files are removed at the end,
also after an error or Ctrl-C. After `qdel` or a job killed at its walltime they can stay:
then remove the folders `silas_ff_*` in `/dev/shm` (if it was the same node) and in
`/work/<user>/silas_cache`.

| | Dual MPCCD (2 panels) | Octal (8 panels) |
|---|---|---|
| buffer of a 1000-shot step (32-bit) | 4 GB | 16 GB |
| job memory (estimate of `full_frame_job`, 16 cores) | ~20 GB | ~48 GB |
| one image (all panels, float32) | 4.2 MB | 16.8 MB |

**File size.** Before compression: (images per step × steps + images per bin × bins) × the image
size above. Per step: 4 statistics × pump states × (1 + references) images, more with
`step_reference` and `pairs` (pairs: on / off and on − off). For example a scan of 51 steps,
2 pump states, one reference, 2 panels: 51 × 4 × 2 × 2 × 4.2 MB ≈ 3.4 GB; with all 8 panels of
an octal ≈ 14 GB. The file is gzip-compressed, which helps
most where the threshold makes pixels 0. The job prints the estimate when it starts.

## The file

`<output>/scans/scan_<id>/fullframe_<id>[_p<panels>].h5`, next to the ROI analysis' files, with
`fullframe_<id>….json` (the info) and `fullframe_<id>…_preview.png` (median pump-on image of
the first and last step and their on / off ratios, assembled).

| In the h5 file | Shape | Content |
|---|---|---|
| `steps/raw` | (step, state, stat, panel, row, col) | the pixel values |
| `steps/per_<R>` | (step, state, stat, panel, row, col) | pixel / R per shot |
| `steps/raw_with_<R>` | (step, state, stat, panel, row, col) | shots with R only (`step_reference`) |
| `steps/pairs_raw`, `steps/pairs_per_<R>` | (step, stat, panel, row, col) | nearest-off pairs, on / off (`pairs`) |
| `steps/pairs_diff_raw`, `steps/pairs_diff_per_<R>` | (step, stat, panel, row, col) | nearest-off pairs, on − off (`pairs`) |
| `bins/raw`, `bins/per_<R>` | (bin, stat, panel, row, col) | time-tool bins, pump on |
| `bins/onoff_raw`, `bins/onoff_per_<R>` | (bin, stat, panel, row, col) | step-wise on / off per bin |
| `bins/diff_raw`, `bins/diff_per_<R>` | (bin, stat, panel, row, col) | step-wise on − off per bin |
| `step_table` | one column per dataset | `x`, `step`, `value`, `runs`, `n_read`, `n_unreadable`, `n_<s>`, `n_<s>_<R>`, `<R>_<s>_q16/_median/_q84` (the reference's quantiles), `n_pairs…` |
| `bin_table` | one column per dataset | `bin`, `t_pulse`, `t_ps`, `t_ps_median`, `n`, `computed` (has images), `n_read`, `n_<R>`, `n_onoff` |
| `shots` | one column per dataset | the shot table: `run`, `tag`, `step`, `value`, `use`, `state`, `read` (in the images), `ref_<R>`, `step_index` (row of `step_table`); with binning the timing columns and `bin` (the bin number, `bin_table/bin`) |
| `point_detectors/<label>` | (shot,) | a point detector's value in every shot, in the rows of `shots` (NaN: not recorded); attributes `detector` (true name), `alias`, `unit` |
| `point_steps` | one column per dataset | per step (the rows of `step_table`): `<label>_<s>_n`, `_mean`, `_std`, `_q16`, `_median`, `_q84` over the shots in the images |
| attribute `info` | JSON | scan (`motor` = alias, `motor_true`), runs, array, `panel_numbers`, `panels`, `detector_info` and `geometry` (to assemble the panels), corrections, keV per ADU, photon energy, states, references, `point_detectors` (label → detector, alias, unit), options, binning, shot counts, `layout`, timing |

The axes: state 0 = `on`, 1 = `off` (or 0 = `all`); stat 0..3 = `q16`, `median`, `q84`, `mean`
(attribute `stats` of every dataset); the step index is the row of `step_table` (sorted by
`x`, the motor value), the bin index the row of `bin_table` (its column `bin` is the bin number,
which the shots' `bin` refers to). Both count from 0; only the viewer's `step=` counts from 1.
Images are in the panels' own pixels (as `read_det_data`), not assembled (see [Assembling the
panels](#assembling-the-panels)); NaN where nothing was computed.

## Working with the results

A short version of the examples in `notebooks/start_here.ipynb` (section "Full-frame analysis").

### With SILAS

In the session (`load_full_frame`), or at home with the SILAS package (needs h5py, numpy, pandas):

```python
from silas.offline.fullframe import FullFrame
ff = FullFrame("fullframe_1234501_p78.h5")         # in the session: ff = load_full_frame(1234501)
ff.steps                                           # the step table (pandas); ff.info: the metadata
ff.info["scan"]["motor"]                           # the scanned motor (alias); "motor_true": true name

step, ref = 10, "i0_1"                             # the ROW of ff.steps; "raw" or a reference label
on = ff.image(ref, step=step, state="on")          # (panels, rows, cols), median; stat="mean" for weak signals
off = ff.image(ref, step=step, state="off", stat="mean")
ratio, lo, hi = ff.onoff(ref, step=step)           # on / off of the medians with errors
diff, lo, hi = ff.difference(ref, step=step)       # on - off of the medians; stat="mean": of the means
ff.step_referenced(ref, step=step)                 # median pixel / median i0_1 (only with step_reference)
ff.image("pairs_i0_1", step=step)                  # nearest-off pairs (only with pairs); "pairs_diff_i0_1": on - off
ff.stack("raw", state="on", stat="mean")           # all steps: (steps, panels, rows, cols)
plt.imshow(ff.assemble(ratio))                     # 2-D image in detector geometry (gaps NaN)

if ff.bins is not None:                            # time-tool bins (with binning)
    i = 3                                          # the ROW of ff.bins (computed 0: no images, NaN)
    ff.bins["t_ps"].iloc[i]                        # its time (ps)
    ff.image(ref, bin=i)                           # pump on; "onoff_i0_1" / "diff_i0_1": step-wise on / off, on - off
ff.close()
```

### Point detectors

```python
ff.point_info                                      # {label: {"detector": true name, "alias", "unit"}}
ff.points["i0_2"]                                  # every shot, in the rows of ff.shots (NaN: not recorded)
shots = ff.shots.join(ff.points)                   # with run, tag, step_index, state, read, bin, ...
used = shots[shots["read"].astype(bool)]           # the shots in the images
used.groupby(["step_index", "state"])["i0_2"].mean()          # state 0 = on, 1 = off
used[used["bin"] == ff.bins["bin"].iloc[3]]["i0_2"].median()  # the shots of a time-tool bin
ff.point_steps["i0_2_on_mean"]                     # per step, already computed (rows of ff.steps)
```

### Without SILAS

h5py and numpy alone (e.g. on a laptop without the package):

```python
import h5py, json
with h5py.File("fullframe_1234501_p78.h5", "r") as f:
    info = json.loads(f.attrs["info"])
    st, stats = info["states"], info["stats"]                 # ["on", "off"], ["q16", "median", "q84", "mean"]
    on = f["steps/per_i0_1"][10, st.index("on"), stats.index("median")]    # (panels, rows, cols)
    x = f["step_table/x"][()]                                 # motor value of every step
    bin_onoff = f["bins/onoff_per_i0_1"][3, stats.index("mean")]           # with binning; bins: no state axis
    i0 = f["point_detectors/i0_2"][()]                        # one value per shot
    step_index, state = f["shots/step_index"][()], f["shots/state"][()]
img = assemble(on, info)                                      # see Assembling the panels
```

Reading one image reads only that image from the file (one compressed chunk per panel), so
large files are fine to browse.

## Assembling the panels

The images are stored per panel, in the panels' own pixels. `ff.assemble(image)` puts them where
they are on the detector, exactly as the [MPCCD viewer](mpccd-viewer.md) and the full-frame viewer
do (`silas.core.geometry`):

- **Where the panels are**: from the detector info of each panel that stpy gives with the data
  (stored in the file's `info["detector_info"]`): the position of the panel's first pixel
  (`mp_posx`, `mp_posy`, µm; the centre of its top-left pixel), its rotation
  (`mp_rotationangle`, degrees, counterclockwise), the pixel size, and the opening of the
  aperture (`mp_manipulator_shift` × `mp_shiftweight` along `mp_stagedirection`).
- **On top of that**, the geometry corrections of the MPCCD array as they were when the
  reduction ran (`info["geometry"]`: the per-panel offsets tuned in the viewer, the rounding and
  the sub-pixel shift); the panel offsets are renumbered to the stored panels.
- **The image**: detector X to the right and Y up, the detector origin (X = Y = 0, the beam axis
  if the detector is centred) in the centre pixel, the panels' pixel size. Every image pixel
  takes the value of the nearest panel pixel (an inverse map, as `ippy.reconstruction`: no holes,
  no pixel used twice); between the panels NaN. Only the stored panels are drawn, so the
  image of panels 7 and 8 is as large as the detector but mostly NaN: crop to the finite part.
- **No rescaling**: the values are keV already (each panel with its own keV per ADU), so unlike
  the raw MPCCD view no gain normalisation is applied.

`ff.assemble` works on anything with the shape (panels, rows, cols): images, ratios, error
images, a mask. Without SILAS, this function (numpy only) gives the same image; it is tested
against `silas.core.geometry` with rotated, shifted panels and every rounding:

```python
import math
import numpy as np


def assemble(panels, info):
    """(panels, rows, cols) -> one 2-D image in detector geometry, as ff.assemble: detector origin
    in the centre pixel, X to the right, Y up, nearest panel pixel, NaN between the panels.
    info: json.loads(f.attrs["info"]) of the full-frame file."""
    geo, det = info["geometry"], info["detector_info"]
    offsets = geo.get("panel_offsets") or {}
    rnd = {"floor": np.floor, "trunc": np.trunc, "rint": np.rint}[geo.get("rounding", "floor")]
    sub_r, sub_c = geo.get("sub_pixel", (0.0, 0.0))
    shift = float(det[0].get("mp_manipulator_shift", 0.0))          # aperture opening (um)
    n, h, w = np.shape(panels)
    pos = []                                                          # (x0, y0, angle, pixel) per panel
    for k, d in enumerate(det):
        off = offsets.get(str(k + 1)) or {}
        a = math.radians(float(d.get("mp_stagedirection", 0.0)))
        s = float(d.get("mp_shiftweight", 0.0)) * shift
        pix = float(d.get("mp_pixelsizex", d.get("mp_pixelsize_x", 50.0)))
        pos.append((float(d["mp_posx"]) + float(off.get("dx_um", 0.0)) + math.cos(a) * s,
                    float(d["mp_posy"]) + float(off.get("dy_um", 0.0)) + math.sin(a) * s,
                    math.radians(float(d["mp_rotationangle"]) + float(off.get("drot_deg", 0.0))), pix))

    def corners(x0, y0, th, pix):                                     # X, Y (um) of the corner pixel centres
        u = np.array([0.0, w - 1, 0.0, w - 1])
        v = -np.array([0.0, 0.0, h - 1, h - 1])
        return (x0 + pix * (u * math.cos(th) - v * math.sin(th)),
                y0 + pix * (u * math.sin(th) + v * math.cos(th)))

    pix0 = pos[0][3]
    cs = [corners(*p) for p in pos]
    half_r = int(math.ceil(max(np.abs(y).max() for _, y in cs) / pix0)) + 2
    half_c = int(math.ceil(max(np.abs(x).max() for x, _ in cs) / pix0)) + 2
    image = np.full((2 * half_r + 1, 2 * half_c + 1), np.nan, np.float32)
    for k, ((x0, y0, th, pix), (x, y)) in enumerate(zip(pos, cs)):
        c_lo = max(int(math.floor(x.min() / pix0)) + half_c - 1, 0)
        c_hi = min(int(math.ceil(x.max() / pix0)) + half_c + 1, image.shape[1] - 1)
        r_lo = max(int(math.floor(-y.max() / pix0)) + half_r - 1, 0)
        r_hi = min(int(math.ceil(-y.min() / pix0)) + half_r + 1, image.shape[0] - 1)
        rr, cc = np.mgrid[r_lo:r_hi + 1, c_lo:c_hi + 1]
        dx = ((cc - half_c) * pix0 - x0) / pix                        # image pixel -> panel pixel
        dy = (-(rr - half_r) * pix0 - y0) / pix
        col = rnd(dx * math.cos(th) + dy * math.sin(th) + sub_c).astype(int)
        row = rnd(dx * math.sin(th) - dy * math.cos(th) + sub_r).astype(int)
        ok = (row >= 0) & (row < h) & (col >= 0) & (col < w)
        image[rr[ok], cc[ok]] = np.asarray(panels[k], np.float32)[row[ok], col[ok]]
    return image
```

## The viewer

```python
view_full_frame(1234501)                           # the reduction of the chosen panels (set_ff_panels)
view_full_frame(1234501, step=10, ref="i0_1")      # where it starts (default: the first reference)
view_full_frame(1234501, ref="none")               # start without a reference
view_full_frame("~/data/fullframe_1234501.h5")     # any file, e.g. a copy at home
```

```bash
python -m silas.offline.fullframe_view fullframe_1234501.h5 --step 10 --ref i0_1   # without the session
```

Three images of one step, assembled in the detector geometry and cut to the chosen panels:
**pump on**, **pump off** and **on / off** or, with the key `d`, **on − off**. The step (or the
time-tool bin with its time) is shown in bold above the images; bin times count from the left-most
bin with images (t = 0 there). The three images zoom and pan together.

The colour scales stay the same from step to step, so the steps can be compared: pump on and pump
off share one scale over all steps (or bins) of both; on / off (on − off) uses the seismic colour
map with 1 (0) in the middle of the map (a two-slope scale). Each range is 0.1–99.9 % of the
pixels of all steps; `c` switches to the full minimum to maximum (a single hot pixel stretches
that). They are computed again (a few seconds) when the reference, the statistic, steps ↔ bins or
on/off ↔ on−off change. `a` lets the images fill the space instead of keeping square pixels. The
viewer starts with the first reference of the reduction.
On the HPC it opens in the browser like the other plots ([Plots on the
HPC](hpc.md#plots-on-the-hpc-in-the-browser)), on the laptop in a window; it needs only the file,
h5py and matplotlib, so it also works at home.

| Key | |
|---|---|
| left / right, up / down | previous / next step (or bin), 10 steps |
| `r` | reference: none → each reference (pixel / R per shot) → none |
| `m` | statistic: median ↔ mean (on / off is then the ratio of the means) |
| `d` | third image: on / off ↔ on − off |
| `b` | scan steps ↔ time-tool bins (only the bins with images) |
| `l` | log colour scale of pump on / off |
| `k` | colour map of pump on / off: viridis → magma → inferno → cividis → gray |
| `c` | colour scales: 0.1–99.9 % of the pixels of all steps ↔ min–max of all steps |
| `a` | aspect: real (square pixels) ↔ fill the space |
| `s` | save the view as png next to the h5 file |

In the **time-tool bins**, pump on is the bin, on / off and on − off the stored step-wise
images (the statistic of every shot / or − the median pump-off image of its step), and pump off the pump-off
image of the step most of the bin's shots come from (pump-off shots are not binned). The title
says which step that is.

## Checks

`tests/test_fullframe.py` (laptop, fake dbpy / stpy): for a simulated scan, every statistic of
every step, state and reference, the pairs, the step reference, the on / off ratios and the
time-tool bins are compared with numpy on the brute-force corrected images (a sample of 4000
pixels per image), the ROI's mean is checked against the ROI analysis, the point detectors per shot
and per step against the fake values, only the chosen panels must be read, and the statistics in
real worker processes must give the same file. It is not part of `check_and_sync.sh` (it takes a
few minutes): run it with `python tests/test_fullframe.py` after changing the reduction.
