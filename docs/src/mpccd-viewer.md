# MPCCD viewer

An MPCCD array consists of separate sensors ("panels"), each read out as its own detector
ID (e.g. an octal: `MPCCD-8N0-3-002-1` … `-8`; a dual: `MPCCD-2B1-M03-001-1`, `-2`). The
viewer puts the panel images where the panels physically sit, with the gaps between them left
empty, shows the image in keV with a histogram of the deposited energy per pixel, and draws
the [ROIs](rois.md). It runs in two ways, with the same window, keys and settings:

| | Online viewer | Offline viewer |
|---|---|---|
| Where | beamline (olpy, the detector now) | HPC (stpy, stored runs) |
| Started with | `start_mpccd_viewer()` | `start_mpccd_viewer(run=...)` |
| Shows | the newest shot or the running average, live | the average of **all** shots of the runs, pump on or pump off |
| Window | a window on the beamline computer | the browser on your laptop ([Plots on the HPC](hpc.md#plots-on-the-hpc-in-the-browser)) |
| Used for | watching the experiment, dark, threshold, geometry | setting ROIs and corrections on old data |

Before trusting the numbers, take a **dark** and check the **photon energy**: see
[Energy calibration, threshold and dark](energy-calibration.md).

## Setting up the array (once)

The BL3 octal MPCCD, `MPCCD-8N0-3-002` (alias `octal`), is part of the
[standard set](detectors.md#the-standard-set). Another array is added with:

```python
run_detectors()                                          # which panel IDs exist?
add_mpccd("MPCCD-2B1-M03-001", alias="dual", n_panels=2) # MPCCD-2B1-M03-001-1, -2
add_mpccd("MPCCD-8-2-001", alias="sim")                  # the simulator's octal: -1 ... -8
show_mpccd("dual")                                       # all settings of the array
```

The array is an entry of `detectors` in the [configuration](configuration.md) with kind
`MPCCD-array`; its alias works everywhere a name is expected. An explicit list also works:
`add_mpccd(["MPCCD-8N0-3-002-1", ..., "MPCCD-8N0-3-002-8"], alias="octal")`.

### Which array the commands use

With more than one array, every command takes the array as `name=` (key or alias): the
viewers, `set_mpccd`, `set_threshold`, `capture_dark`, the ROI commands and the scan analysis.
Without it they use the **default array**:

```python
use_mpccd()            # list the arrays, their number of ROIs and the default (*)
use_mpccd("dual")      # make dual the default
```

`start_mpccd_viewer(name="dual")` also makes `dual` the default, so `add_roi`,
`set_threshold`, … then go to the array on the screen. `show_rois()` says which array it
shows and lists other arrays that have ROIs.

## Starting it: one command for both

`start_mpccd_viewer` starts the online viewer, or the offline viewer when `run=` is given
(without `replay=True`). Both take the same arguments; an argument that only means something
for the other viewer is ignored, and the viewer says so in its terminal, e.g.
`online viewer: ignored max_tags (offline only)`.

| Argument | Default | Online viewer | Offline viewer |
|---|---|---|---|
| `name` | the default array | the MPCCD array (key or alias); becomes the default | same |
| `workers` | online 4, offline 6 | MPI reader processes (0: one process reads and draws) | processes that load and average (0 or 1: one; at most the job's cores) |
| `test` | `True` on the laptop | test data instead of the detector | ignored (stored runs are read with stpy) |
| `run` | – | – | a run number or a list of runs |
| `replay` | `False` | with `run=`: play the (first) run as if live | – |
| `rate` | 10 | replay: shots per second | ignored |
| `max_tags` | all | ignored | the first `max_tags` tags per run |
| `every` | 1 | ignored | every `every`-th tag |
| `cache` | `"auto"` | ignored | where the loaded shots are kept: `"auto"`, `"ram"` or `"work"` |

`workers` means the same everywhere in SILAS: the number of processes that share the
work (`start_mpccd_viewer`, `start_scan_viewer`, `analyse_scan`). Use at most the cores you
have (on the HPC: of your `bt_node`).

## Online viewer (beamline)

```python
start_mpccd_viewer()              # new terminal, MPI: 1 display + 4 reader processes
start_mpccd_viewer(workers=8)     # more readers
start_mpccd_viewer(workers=0)     # a single process (no MPI)
start_mpccd_viewer(test=True)     # test data instead of the detector
start_mpccd_viewer(name="octal")  # another array (and make it the default)
```

The viewer reads the newest shots, corrects them (dark, threshold) and shows either the
**latest** shot or the **average** of all shots since the last reset (`a` switches, `r`
resets). Stop it with `q` or Ctrl-C in its terminal.

If the window does not open because of the matplotlib backend (e.g. "Failed to import any
of the following Qt binding modules"), the viewer tries the other backends by itself; to
fix one, `set_mpccd("display.backend", "TkAgg")`.

### Test data (laptop)

On the `local` profile the viewer uses **test data** by default: the simulator panels
saved by `tests/diag_mpccd_geometry.py`, replayed at 60 Hz as pedestal (≈ 100 ADU) + noise +
single photons that follow the saved pattern. The photons have the configured photon energy
(12 keV if none is set) and some share their charge with a neighbour, so the histogram shows
1- and 2-photon peaks. No SACLA library is involved, so it runs on a laptop in smoke mode.
Take a test dark first (`capture_dark(test=True)`), otherwise the pedestal shifts everything
by about 6.6 keV.

### How the reading is shared (MPI)

SACLA runs at up to 60 Hz. Reading 8 panels of 2 MB per shot is the slow part, so it is
spread over several processes started with `mpirun` (the command is the profile's `mpirun`
setting):

- rank 0 shows the window;
- every other rank reads, corrects (dark, threshold) and adds up its own share of the shots,
  in ADU: reader *k* of *N* takes the shots whose `(tag / tag_step) mod N` equals *k*, so
  together they cover every shot without talking to each other;
- every `update_s` seconds (default 0.2, i.e. 5 updates per second) each reader sends its
  sum and its newest shot to rank 0, which averages, converts to keV and draws.

`missed` in the status line counts shots that no reader got to before newer ones arrived.
For watching the detector a few missed shots do not matter; more readers lower the number.
Without MPI (e.g. on a laptop) one process reads and draws, so it misses many shots.

## Offline viewer (HPC)

The offline viewer shows stored runs: it reads every shot once and then shows their
**average**, so ROIs, threshold and geometry can be set on old data exactly as at the
beamline. On a compute node (`bt_node`, see [Working on the HPC](hpc.md)), in `bt`:

```python
start_mpccd_viewer(name="octal", run=1234501)                     # one run
start_mpccd_viewer(name="octal", run=[1234501, 1234502])          # several runs together
start_mpccd_viewer(name="octal", run=1234501, workers=8)          # more workers (default 6)
start_mpccd_viewer(name="octal", run=1234501, max_tags=300)       # at most 300 tags per run
start_mpccd_viewer(name="octal", run=1234501, every=5)            # every 5th tag
start_mpccd_viewer(name="octal", run=1234501, cache="work")       # keep the loaded shots on /work
```

The arguments are in the table [above](#starting-it-one-command-for-both).

It runs in the session's terminal. When it starts it prints how many shots it will load, the
cache size and place, its loading progress, and then the `ssh` line to copy into a terminal
on the laptop and the link to open in the browser
([Plots on the HPC](hpc.md#plots-on-the-hpc-in-the-browser)). Stop it with `q` in the page or
Ctrl-C in the terminal; the session prompt comes back.

### What is loaded

For every tag of the runs (or the ones chosen with `max_tags` / `every`), as in the
[scan analysis](scans.md#which-shots-are-used):

- X-rays off (`xfel_shutter` = 0) or a bad beam status: not loaded;
- the pump state from the pump detector (`set_pump`, default `optical_shutter` = 1 is pump
  on): pump on or pump off; unknown: not loaded;
- the raw panels of the other shots are read with stpy, spread over the `workers`.

The status line shows the counts:
`runs <runs>: <loaded> of <tags> shots loaded (X-rays off <n>, pump unknown <n>, unreadable <n>); on <n> / off <n>   SHOWING: PUMP ON`.

### What is shown

- the **average** of all loaded shots of one pump state, corrected with the current dark and
  threshold, in keV (or ADU, `u`);
- `w` switches between **pump on** and **pump off**: where the two differ, the pump changes
  the signal; that is where ROIs go. Without a pump detector (`set_pump(None)`) all shots
  are shown together;
- the histogram is the sum of the single-shot histograms (keV) of all shots of the pump
  state shown (every 2nd row and column of every shot);
- the ROIs with their table, as online; the mean per pixel is that of the average.

Nothing is live: the image only changes when a setting changes. A change of dark, threshold or
calibration (keys `d`, `t`, `+`, `-`, or `set_threshold(...)` etc. in the session)
**recomputes** the averages and the histogram from the loaded shots, with the same `workers`:
a few seconds for one run. The previous image stays until the new one is ready (`computing
...` in the status line, `averages ready (x s)` in the terminal). Geometry, colour and ROI
changes are immediate. `a`, `r`, `p`, `<` and `>` are listed but do nothing (the message
line says so): there is only the average.

### Where the loaded shots are kept

Recomputing needs every shot again, so the raw shots are kept (as 16-bit integers when the
data are whole ADU numbers, else 32-bit floats):

| | RAM (`/dev/shm`) | /work |
|---|---|---|
| Chosen | if the shots fit in half of the job's free memory (`cache="auto"`), or `cache="ram"` | otherwise, or `cache="work"` |
| Folder | `/dev/shm` | `/work/<user>/silas_cache`, or `cfg.set("paths.cache", ...)` |
| After the viewer | deleted | **kept**: the same runs with the same options open again without loading (until /work deletes them, 90 days after the last access) |

Sizes: about 1 MB per panel per shot, so 1000 shots of a dual are about 2 GB and 1000 shots
of an octal about 8 GB. For whole octal runs ask for enough memory (`bt_node big`, 64 GB) or
let it go to /work. RAM caches of a viewer that was killed are removed the next time a viewer
starts.

### Replay: the online viewer on stored data

```python
start_mpccd_viewer(name="octal", run=1234501, replay=True, workers=4, rate=30)
```

`replay=True` plays **one** stored run as if it came from the detector, at `rate` shots per
second, in a loop: this is the online viewer (latest / average, its MPI readers with
`workers`, missed shots), only fed from stpy. It is for trying the online viewer, e.g. with
all 8 panels of an old run. The tag shown is the run's own tag.

## The window

- **Top**: status lines:
  - array, data source, `latest` / `average` (offline: always `average`);
  - online: shot tag, number of shots averaged, shots per second, missed shots, update
    interval, colour map; offline: the runs, the shot counts and the pump state shown;
  - corrections: dark (on / off, file), threshold in keV and photons, units;
  - energy: photon energy and where it comes from, calibration, ADU per photon of each panel;
  - geometry: aperture shift, tuned panels, selected panel, and the last message.
- **Image**: the assembled detector with real-space axes in mm (X right, Y up, origin =
  detector origin). Panel gaps are light grey, panel outlines and numbers are drawn on top.
- **Bottom**: histogram of the deposited energy per pixel in keV (log scale), always from
  **single shots**: online the newest shot, or in average mode all shots shown since the last
  reset added up; offline all shots of the pump state shown. Red: the threshold; green
  dotted: 1, 2, 3 × the photon energy; dashed (online, latest mode): the colour limits. See
  [Checking it with the histogram](energy-calibration.md#checking-it-with-the-histogram).
  With `u` (ADU) it is the histogram of the image shown instead.
- **Right**: the key list (`h` hides it) and, below it, the ROI table (see [ROIs](#rois)).
- **Toolbar** (bottom of the window or page): zoom box, pan, and the read-out of the pixel
  under the mouse. In the browser, click on the figure once so that it receives the keys.

### Zoom and pixel values

- the **mouse wheel** zooms in and out around the cursor; the toolbar's zoom (magnifier)
  and pan (cross) buttons work as usual; `z` shows the whole detector again. The zoom stays
  while the image updates.
- moving the mouse over the image shows, in the toolbar:

  ```
  X=  12.350 mm  Y= -20.100 mm   pixel (row 402, col 247) from origin   panel 3 (row 402, col 247)   value 9.87 keV
  ```

  the detector position in mm, the pixel counted from the detector origin (rows down,
  columns right; the numbers [ROIs](rois.md) are given in), the panel and the pixel in
  that panel's own image, and the value shown. In a gap it says `gap`.

### Keys

| Key | Action | Online | Offline |
|---|---|---|---|
| `h` | key list on / off | ✓ | ✓ |
| `a` | average of all shots / latest shot (`display.mode`) | ✓ | – (listed) |
| `r` | reset the average and the histogram | ✓ | – (listed) |
| `p` | pause the display (reading continues) | ✓ | – (listed) |
| `<`, `>` | update slower / faster (`display.update_s` × 2 / ÷ 2, 0.05 to 5 s) | ✓ | – (listed) |
| `w` | pump on / pump off | – (listed; says it is offline only) | ✓ |
| `z` | reset the zoom (mouse wheel: zoom around the cursor) | ✓ | ✓ |
| `l` | log / linear colour scale | ✓ | ✓ |
| `c`, `C` | next / previous colour map | ✓ | ✓ |
| `v` | freeze the colour limits / back to automatic | ✓ | ✓ |
| `o` | panel outlines on / off | ✓ | ✓ |
| `i` | ROIs and their table on / off | ✓ | ✓ |
| `s` | save a snapshot (PNG + numpy file) to `<output>/mpccd/` | ✓ | ✓ |
| `q` | quit (also stops the reader / worker processes) | ✓ | ✓ |
| `d` | dark subtraction on / off | ✓ | ✓ (recomputes) |
| `t` | threshold on / off | ✓ | ✓ (recomputes) |
| `+`, `-` | threshold × 1.25 / ÷ 1.25 (keV, saved in the configuration) | ✓ | ✓ (recomputes) |
| `u` | units: keV / raw ADU (each panel in its own ADU, not calibrated) | ✓ | ✓ |
| `1` … `8` | select a panel for geometry tuning (`0`: none) | ✓ | ✓ |
| arrows | move the selected panel by 1 pixel (with shift: 10 pixels) | ✓ | ✓ |
| `[`, `]` | rotate the selected panel by −/+ 0.05° | ✓ | ✓ |
| `m`, `M` | aperture (manipulator) shift −/+ 100 µm | ✓ | ✓ |
| `x` | clear the tuning of the selected panel (of all panels if none is selected) | ✓ | ✓ |

Both viewers list all keys (`h`); a key that only works in the other viewer is marked
`(online)` or `(offline)` there and only writes a message when pressed. A pump on / off split
live (`w` online) is not there yet: the pump flag reaches the SyncDB seconds after the image,
so the shots would have to wait for it; the [scan viewer](scans.md) sorts by pump state live.

Snapshots are named by time and tag online (`snapshot_<time>_tag<tag>`) and by runs and pump
state offline (`snapshot_<time>_runs1234501_on`).

## ROIs

[Regions of interest](rois.md) set in the session are drawn on the image within a second.
They belong to an array: with the viewer open on `dual`, `add_roi` without `name=` goes to
`dual` (see [Which array the commands use](#which-array-the-commands-use)).

```python
add_roi("peak", rows=(102, 283), cols=(200, 266))   # pixels from the origin, as in the toolbar
show_rois()
```

- orange rectangles with the ROI's name; grey if the ROI covers no panel pixel (gap / outside);
- the table at the bottom right gives, for every ROI, its number of panel pixels, the panels
  it covers and its **mean per pixel** in the shown image (keV or ADU; newest shot, average,
  or offline the average of the pump state shown), and which panels the ROIs need. Pixel
  counts and panels are only known to the viewer (it has the detector geometry), not to
  `show_rois()`;
- if the array has no ROIs, the table says so;
- `i` hides and shows them.

The mean per pixel is a quick check while choosing a ROI (is the peak inside? is the
background flat?); the [scan analysis](scans.md) uses the same pixels and corrections.

## Settings: session and viewer stay in sync

Every key that changes a setting writes it to the configuration file, and the viewer
re-reads the file when it changes. So settings can be changed from either side, online and
offline:

```python
set_threshold(2.0, name="octal")         # keV, and switch it on
set_photon_energy(9.9)
set_mpccd("corrections.dark", False, name="octal")
set_mpccd("display.cmap", "magma")
set_mpccd("display.update_s", 0.5)
set_mpccd("geometry.manipulator_shift_um", 1500)
show_mpccd()
```

and the running viewer follows within a second. Settings chosen with keys are still there
the next time the viewer starts.

| Setting | Meaning |
|---|---|
| `corrections.dark` | subtract the dark |
| `corrections.dark_file` | dark file (set by `capture_dark`) |
| `corrections.threshold`, `corrections.threshold_keV` | set pixels below the threshold (keV) to 0 |
| `calibration.ev_per_absgain_adu` | keV per ADU of a panel = `mp_absgain` × this / 1000 (3.65) |
| `display.units` | `keV` or `ADU` |
| `display.mode` | `average` or `latest` (online) |
| `display.cmap`, `display.log` | colour map, log scale |
| `display.auto_limits`, `display.percentiles`, `display.vmin`, `display.vmax` | colour limits |
| `display.outlines` | panel outlines |
| `display.rois` | draw the ROIs and their table |
| `display.update_s` | seconds between updates (online) |
| `display.backend` | matplotlib backend, e.g. `"TkAgg"` (`null`: the first that works; WebAgg on the HPC) |
| `rois` | the ROIs: `{"name": {"rows": [r0, r1], "cols": [c0, c1]}}`, pixels from the origin ([ROIs](rois.md)) |
| `geometry.manipulator_shift_um` | aperture opening (see below) |
| `geometry.panel_offsets` | fine-tuning per panel: `{"3": {"dx_um": 50, "dy_um": 0, "drot_deg": 0.1}}` |
| `geometry.rounding`, `geometry.sub_pixel` | which neighbouring panel pixel an image pixel takes (see below) |
| `tag_step` | tag difference between consecutive shots (1 at 60 Hz, 2 at 30 Hz) |

The default array is the configuration's top-level `default_mpccd` (`use_mpccd`).

## Corrections

Applied to every shot before it is averaged, in this order:

1. **dark subtraction**: the mean image without X-rays is subtracted;
2. **threshold**: pixels below `threshold_keV` are set to 0, which removes the noise floor.
   The keV threshold is converted once to ADU for every panel, so the shots are compared
   as they are and never rescaled.

The shots stay in ADU of each panel; they are converted to keV (`mp_absgain` ×
`ev_per_absgain_adu` per panel) only when the image is drawn. This replaces the former gain
normalisation to panel 1. Details: [Energy calibration, threshold and dark](energy-calibration.md).

Changing a correction or the calibration resets the average and the histogram (online) or
recomputes them from the loaded shots (offline), so they never mix shots corrected
differently.

### Dark

With the shutter closed (no X-rays on the detector):

```python
capture_dark(shots=300)
```

averages 300 shots, saves them to `<output>/darks/dark_<array>_<time>.npz`, sets the file as
`dark_file` and switches dark subtraction on. A running viewer picks it up immediately.
**Take a dark at the start of every beamtime, for every user** (the file is set in your own
configuration); why and when: [The dark: do this first](energy-calibration.md#the-dark-do-this-first).

`capture_dark` reads the live detector, so it is online only. Offline, data that were
already offset-corrected when they were recorded are used without a dark:
`set_mpccd("corrections.dark", False, name="octal")`.

## Geometry

The panel positions come from SACLA with every image (`mp_posx`, `mp_posy` in µm,
`mp_rotationangle`, pixel size, gain `mp_absgain`), so nothing has to be measured. The rules, taken from
SACLA's "MPCCD octal image assembly algorithm" and checked on the octal MPCCD of the olpy
simulator:

- a panel's position is that of the centre of its top-left pixel (row 0, column 0);
- the detector frame has X to the right and Y up (image rows go down);
- the panel is rotated about that pixel, counterclockwise in the image;
- the assembled image has the panel pixel size and is centred on the detector origin.

Only this convention places the 8 simulator panels without any overlap; the result agrees
with SACLA's own `ippy.reconstruction` (`tests/check_geometry_ippy.py`).

The positions are measured with the detector's central **aperture closed**. If the
aperture is opened, the panel groups move apart by `shift weight × L` along their stage
direction; set L with `geometry.manipulator_shift_um` (or `m`/`M`). Offline the value is
read from the data; online it has to be set by hand.

Because the panels are rotated by almost but not exactly 90°, an image pixel often lies
almost exactly between two panel pixels. `geometry.rounding` and `geometry.sub_pixel` decide
which of the two is taken; they never move a panel by more than one pixel.
`tests/check_geometry_ippy.py` compares the possible settings with `ippy.reconstruction`;
`floor` (the default) agrees with it for 97.6 % of the pixels.

If a panel still looks misplaced (rings not continuous across a gap), select it with its
number and nudge it with the arrow and bracket keys; the offsets are stored in
`geometry.panel_offsets` and used from then on.
