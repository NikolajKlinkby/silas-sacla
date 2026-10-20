# Setting up a beamtime, step by step

From an empty account to the analysis: ssh, the code and its packages, the configuration, the
run log, the time tool (optional), the energy calibration with threshold and dark, the ROIs, and
how the analysis is meant to run. Each step ends with a check, so a mistake shows up where it is
made and not at the end, and links to the page with the details.

The steps are written for the **HPC** (stored runs; `bt_node`, then `bt`, or a
[Jupyter notebook](jupyter.md)). **At the beamline** the same commands work live
([Working at the beamline](beamline.md)): where they differ, both are given.

| Step | What | Where |
|---|---|---|
| [1](#1-ssh-to-the-hpc) | ssh from the laptop to the HPC | laptop |
| [2](#2-the-code-and-its-packages) | code and packages on the HPC | laptop, HPC login node |
| [3](#3-the-configuration) | output folder, beamline, first run, photon energy, detectors | session |
| [4](#4-the-run-log) | the run log and the scan table | session or batch job |
| [5](#5-the-time-tool-optional) | time-tool correction (optional) | session or batch job |
| [6](#6-energy-calibration-threshold-and-dark) | dark, photon energy, energy calibration, threshold; look at the images | session |
| [7](#7-rois) | regions of interest | session |
| [8](#8-the-analysis) | how the analysis runs during and after the beamtime | session, batch jobs |

## 0. Starting from a clean slate (optional)

To try the whole guide on an account that was used before, remove what SILAS made, **not
the whole home folder**: `~/.ssh` (the laptop's key login), `~/.bashrc` and the other dot-files
belong to the account, and the HPC home is not backed up.

Keep first what took long to make or cannot be made again: the experiment config
(`~/SILAS/config/hpc.json`: ROIs, time-tool background and calibration, dark files), and
results in the output folder (full-frame reductions, time-tool calibrations, copies of SACLA's
timing-monitor results). For example:

```bash
# HPC login node: keep a copy, then remove what SILAS made
mkdir -p /UserData/$USER/keep
cp -p ~/SILAS/config/*.json /UserData/$USER/keep/
tar czf /UserData/$USER/keep/output_$(date +%Y%m%d).tgz -C /UserData/$USER <old output folder>   # if wanted

rm -rf ~/SILAS ~/SILAS_py37 ~/SILAS_jupyter ~/SILAS_ipython
sed -i '/# SILAS shortcuts/d' ~/.bashrc         # the line install_hpc_env.sh added
rm -rf /UserData/$USER/<old output folder>
```

Log out and in again: the login message no longer mentions SILAS.

## 1. ssh to the HPC

Once per laptop. The HPC login node `xhpcfep.hpc.spring8.or.jp` is reached only with the
**SPring-8 / SACLA VPN** on. The scripts (`sync_to_hpc.sh`, `hpc_jupyter.sh`, `check_and_sync.sh`,
`fetch_sacla.sh`) and the tunnels that the plots and Jupyter print use the ssh name **`sacla_HPC`**.

**With the ssh config (recommended).** Add to `~/.ssh/config` on the laptop:

```
Host sacla_HPC
    HostName xhpcfep.hpc.spring8.or.jp
    User <your HPC user name>
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 60
```

and log in with a key instead of the password every time:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519      # skip if the key exists; a passphrase is recommended
ssh-copy-id -i ~/.ssh/id_ed25519.pub sacla_HPC  # VPN on; asks for the HPC password once
```

`ssh-copy-id` adds the key to `~/.ssh/authorized_keys` on the HPC. The home folder is the same on
the compute nodes, so the tunnels to a compute node (`-J sacla_HPC <user>@<node>`) use the same
key. With a passphrase, `ssh-add ~/.ssh/id_ed25519` once per login session saves typing it.

**Without the ssh config.** Every laptop script takes `--user <name>` and then connects to
`<name>@xhpcfep.hpc.spring8.or.jp` directly:

```bash
bash scripts/sync_to_hpc.sh --user <name> --go
bash scripts/check_and_sync.sh --user <name>
bash scripts/hpc_jupyter.sh --user <name> --start
```

(`sync_to_hpc(go=True, user="<name>")` in the session.) The tunnels printed by the HPC also show
the line for this case (`-J <name>@xhpcfep.hpc.spring8.or.jp`). Without a key, ssh asks for the
password at every connection.

**Check:**

```bash
ssh sacla_HPC hostname          # VPN on: prints the login node's name without asking for a password
```

## 2. The code and its packages

Once per computer; repeat the sync whenever the code changed. Details:
[Working on the HPC](hpc.md#one-time-setup), [Jupyter notebooks](jupyter.md#one-time-setup).

On the **laptop** (VPN on, in the SILAS folder):

```bash
bash scripts/fetch_hpc_packages.sh        # IPython and tornado for Python 3.7 -> third_party/py37/
bash scripts/fetch_jupyter_packages.sh    # optional: JupyterLab and the notebook widgets -> third_party/jupyter_py37/
bash scripts/sync_to_hpc.sh               # dry run: what would be copied
bash scripts/sync_to_hpc.sh --go          # copy the code (and the packages) to ~/SILAS
```

On the **HPC login node**:

```bash
ssh sacla_HPC
bash ~/SILAS/scripts/install_hpc_env.sh        # dry run
bash ~/SILAS/scripts/install_hpc_env.sh --go   # shortcuts in ~/.bashrc, packages, Jupyter if fetched
exit                                                 # log in again to get the shortcuts
```

**Check:**

```bash
ssh sacla_HPC          # the login message: "SILAS: bt_node to get a compute node, ..."
bt_node                # an interactive job on a compute node
bt_check               # packages and SACLA libraries: IPython, tornado and dbpy/stpy/olpy found
bt                     # the session starts with IPython (In [1]:)
```

At the beamline the code comes from the HPC instead:
`bash fetch_from_hpc.sh --go`, then `bash ~/SILAS/scripts/install_beamline_env.sh --go`,
and `bl` starts the session ([Working at the beamline](beamline.md)).

## 3. The configuration

The experiment config of the profile (`config/hpc.json` on the HPC): output folder, beamline,
first run, photon energy, the standard detectors and the scan settings. Details:
[Configuration](configuration.md#setting-it-up), [Detectors and aliases](detectors.md).

In the session (`bt`):

```python
setup_config(beamline=3)
```

It asks:

| Question | Answer |
|---|---|
| first run of the beamtime | the first run to look at, e.g. `1234400` (empty: set it later with `cfg.set("first_run", N)`) |
| photon energy in keV | e.g. `9.9`, or empty to take it from each run's record |
| output folder | a name under `/UserData/<user>/`, e.g. `pcmo_2026` (empty: `tmp`) |

(Or all at once: `setup_config(beamline=3, first_run=1234400, photon_energy_keV=9.9,
output="pcmo_2026")`; from bash: `bash scripts/setup_config.sh --beamline 3 ...`.)

### Point detectors

The standard BL3 set: `optical_shutter`, `laser_shutter` (the X-ray shutter), `i0_1`, `i0_2`,
`delay_m`, `delay_tt`. Add what this experiment needs beyond it, e.g. a fluence motor:

```python
find_equipment("*bl_3*motor*")                                           # the names (live / HPC)
add_point_detector("xfel_bl_3_st_2_motor_4/position", alias="fluence_1", unit="pulse")
```

### Image detectors

**MPCCD arrays.** `setup_config(beamline=3)` adds the two BL3 arrays: `octal`
(`MPCCD-8N0-3-002`, 8 panels, the default array) and `dual` (`MPCCD-2B1-M03-001`, 2 panels,
30 Hz: tag step 2, threshold 2 keV). Another array is added from its panel IDs
([MPCCD viewer](mpccd-viewer.md#setting-up-the-array-once)):

```python
run_detectors(1234400)                                     # the detector IDs recorded in a run
add_mpccd("MPCCD-8N0-3-002", alias="octal", n_panels=8)    # panels MPCCD-8N0-3-002-1 ... -8
set_mpccd("tag_step", 2, name="dual")                      # an array that records every 2nd tag (30 Hz)
use_mpccd("octal")                                         # the array the commands use without name=
show_mpccd()                                               # all settings of the default array
```

**Other image detectors** (cameras) are only listed with an alias, e.g. the time-tool camera:

```python
add_detector("OPAL-123456", alias="tt_camera", kind="camera")
set_tt_camera("OPAL-123456")      # the time tool's camera, if not the first OPAL / IPX / CHET of the run
```

**Check:**

```python
cfg.show()                 # beamline, first run, photon energy, output folder
show_detectors()           # point detectors with aliases, the image detectors
show_mpccd()               # the default array: panels, corrections, display
run_summary(1234400)       # the run record of the first run: SACLA can be read
run_detectors(1234400)     # its detectors: the MPCCD arrays and the camera are in it
```

## 4. The run log

The table of all runs and scans from `first_run` on; everything else finds runs and scans in it.
Details: [Run logger](run-logger.md).

```python
make_runlog()                        # HPC: all finished runs from first_run (6 processes)
```

A whole beamtime is better done as a batch job (it continues where it stopped; 24 h, 6 cores):

```bash
qsub -v LAST_RUN=1234999 ~/SILAS/scripts/runlog_job.sh
tail -f ~/SILAS/output/runlog_job.log
```

During a beamtime it can follow the new runs for much longer in the `long` queue (up to 72 h and
12 cores; the `-l` options replace the job's own):

```bash
qsub -q long -l walltime=72:00:00 -l select=1:ncpus=6:mem=16gb ~/SILAS/scripts/runlog_job.sh
```

At the beamline: `start_online_logger()` once at the start (a new terminal; leave it open).

**Check:**

```python
show_runlog()              # the last 20 runs: status, tags, comment
show_scans()               # the scans: runs, motor, range, steps, complete or not
scan_info()                # the newest scan in detail (motor, range, repeated or missing steps)
```

Every scan should have its motor (`delay_m` for a time scan); a scan with "motor not known" did
not find its motor among the point detectors (add it, then `make_runlog()` again).

## 5. The time tool (optional)

Only for time scans where the arrival time of the pump has to be corrected shot by shot (time-tool
binning). Needs a run with timing shots, a background run and a calibration scan of the time-tool
delay motor. Details: [Time tool](timetool.md).

**5.1 Camera and ROI.** A run with timing shots (X-rays and laser on):

```python
start_tt_viewer(run=1234501)        # step through the shots: arrow keys, h help
set_tt_roi(0, 1919, 160, 220)       # x0, x1, y0, y1 around the band with the edge (or e: draw it)
set_tt_binning("rows")              # the trace along x
```

**5.2 Background.** A run without X-rays on the sample (or `shots="background"`: only the shots
flagged as X-rays off):

```python
set_tt_background(run=1234479)      # beamline: capture_tt_background(shots=300) with the X-rays blocked
start_tt_viewer(run=1234479)        # check: no shot of the background run shows an edge
```

**5.3 The edge.** Back on the timing run: the corrected trace has one clear step, the edge line
is green (good) for most timing shots.

```python
start_tt_viewer(run=1234501)
set_tt_edge(direction="rising", window=(600, 1800))   # only if needed: direction, search window
```

**5.4 Calibration.** The calibration scan (the time-tool delay motor, one step per run):

```python
calibrate_tt(1234480)               # the scan containing run 1234480 (run log)
set_tt_time(ps_per_pulse=0.006671)  # pulse -> ps of the delay stage, if not this one
```

Check `calibration.png`: the step medians lie on the line, the residuals are small, no step was
left out for too few good edges.

**5.5 The edge of every shot.** The time-tool log, as a batch job (it follows new runs; 24 h,
6 cores):

```bash
qsub ~/SILAS/scripts/tt_logger_job.sh            # beamline: start_tt_logger()
tail -f ~/SILAS/output/tt_logger_job.log
```

For a whole beamtime, in the `long` queue (up to 72 h):

```bash
qsub -q long -l walltime=72:00:00 -l select=1:ncpus=6:mem=16gb ~/SILAS/scripts/tt_logger_job.sh
```

**5.6 Binning by default (not necessary).** The scan analysis bins by the time tool when asked:
`analyse_scan(1234501, tt=True, bin_ps=0.05)`, and the same options in `full_frame_job` and
`reduce_full_frame`. `set_tt_bins` only sets the default for when they are not given:

```python
set_tt_bins(on=True, bin_ps=0.05)   # analyse_scan(...) and full_frame_job(...) then bin by default
```

**Check:**

```python
tt_logger_status()          # which runs are done
df = load_tt(1234501)       # per tag: edge, good, shift_ps
df["good"].mean()           # fraction of good edges
show_tt()                   # all time-tool settings
```

## 6. Energy calibration, threshold and dark

The MPCCD values are converted to keV deposited per pixel; the dark, the photon energy, the
calibration and the threshold decide whether these numbers are right. Look at the images while
doing it. Details: [Energy calibration, threshold and dark](energy-calibration.md),
[MPCCD viewer](mpccd-viewer.md).

**6.1 The dark.** The mean image without X-rays, subtracted from every shot. On the HPC, from a
**dark run** (recorded with the X-rays blocked):

```python
start_mpccd_viewer(run=1234470)     # first look: no photons in the dark run?
set_dark(run=1234470)               # average all its shots, save the dark, set it (dark subtraction on)
set_dark(run=1234470, shots="xray_off")   # only shots with the X-ray shutter closed
set_dark(file="/UserData/.../darks/dark_....npz")   # a saved dark again (e.g. a colleague's)
```

At the beamline, live, with the X-ray shutter **closed**: `capture_dark(shots=300)`.

Check with the dark run in the viewer: the histogram (`a`: single shots) is one narrow peak at
0 keV. Stored data can already be offset-corrected by SACLA; the dark then is close to 0 and
removes what is left.

**6.2 The photon energy.**

```python
energy_info(1234501)                # the photon energy used, where it comes from, ADU per photon per panel
set_photon_energy(9.9)              # only if the run record is wrong or missing (None: from the record)
```

**6.3 Look at the images and the energy calibration.** A run with X-rays:

```python
start_mpccd_viewer(run=1234501)     # the average of the run's shots, pump on (w: pump off)
```

In the window: the panels sit where they should (outlines; the Bragg peaks or rings continue
across the gaps), `a` switches average / single shots, `u` keV / ADU, `d` dark on / off. The
histogram below the image shows single shots: the 1-photon peak must sit on the first green
line (the photon energy). If it sits at P keV instead of E:

```python
set_energy_calibration(3.65 * E / P)   # new = old x E / P
```

**6.4 The threshold.** Removes the readout noise (about 0.15 keV per pixel):

```python
set_threshold(1.0)                  # keV; in the viewer: t on / off, + / - to change it
```

The red line in the histogram is the threshold: above the noise peak, below the 1-photon peak
(or above a fluorescence line that should not count).

**Check:** in the viewer's status line: dark on (with its file), threshold on (keV and photons),
the photon energy and its source; the histogram: noise at 0, photon peak at E.

## 7. ROIs

Rectangles on the MPCCD array (Bragg peaks, diffuse features, a background region) that the scan
analysis follows through a scan. Details: [Regions of interest](rois.md).

```python
start_mpccd_viewer(run=1234501)     # a run of the scan; w: pump on / off shows where the pump acts
```

Read the top-left and bottom-right corners of a region from the toolbar
(`pixel (row, col) from origin`), then:

```python
add_roi("bragg_002", rows=(290, 330), cols=(179, 225))   # first and last row / column, from the origin
add_roi("background", rows=(285, 335), cols=(240, 295))
```

**Check:** the viewer draws the rectangles within a second; the table on the right shows each
ROI's pixels, panels and mean keV per pixel. `show_rois()` lists them.

## 8. The analysis

How the pieces are meant to work together during the beamtime and after it. Details:
[Scan analysis](scans.md), [Full-frame reduction](fullframe.md).

### What runs all the time

Two loggers follow the beamtime, each in its own terminal or batch job, and everything else reads
their files:

| Logger | Beamline (live) | HPC (stored runs) |
|---|---|---|
| run log: runs, scans, motors | `start_online_logger()` | `runlog_job.sh` (step 4) |
| time-tool log: the edge of every shot (only with the time tool) | `start_tt_logger()` | `tt_logger_job.sh` (step 5.5) |

The online loggers write at the beamline (`output/beamline/`), the HPC ones on the HPC; each place
keeps its own, and the HPC ones are the complete record (every shot of every finished run). They
continue where they stopped when started again.

### The reference (I0)

The ROI signal of each shot is divided by an intensity monitor, so changes of the X-ray pulse
energy do not show up as signal. `setup_config(beamline=3)` sets both BL3 photodiodes; choose
yours:

```python
set_references("i0_1")              # one reference (alias of a point detector)
set_references("i0_1", "i0_2")      # several: every result is made for each of them
set_references()                    # none: unreferenced only
set_pump("optical_shutter", on_value=1)   # which shots are pump on (the BL3 default)
```

The analysis always also gives the unreferenced values. A shot without a value for a reference
is left out of everything computed with that reference.

### At the beamline: the live ROI view

```python
start_scan_viewer()                 # follows the newest scan: ROI signal per step, pump on / off and on / off
```

It shows, while the scan runs, the mean keV per pixel of every ROI for every step (pump on solid,
off dashed) and their ratio, from the shots the readers catch from the online buffer (some are
lost when it is busy: the status line counts them). It is not referenced and not time-tool
corrected: it is for deciding during the beamtime whether a scan shows something, whether to
repeat it, move on or change the ROIs. ([Online: the scan viewer](scans.md#online-the-scan-viewer))

### On the HPC: analyse_scan

```python
analyse_scan(1234501, max_tags=100)          # a quick look: 100 shots per run
analyse_scan(1234501)                        # every shot of the scan
analyse_scan(1234501, tt=True, bin_ps=0.05)  # with time-tool binning
```

The analysis of record for each scan, once its runs are stored: **every** shot (none lost), the
**medians** with their 16-84 % spread instead of means, referenced **shot by shot** by each
reference, pump on / off as the ratio of the medians, optionally per step referenced
(`step_reference=True`), nearest-off pairs (`pairs=True`) and time-tool bins (`tt=True`). It
saves the tables (`steps.csv`, `shots.csv`, `scan_<id>.h5`) and the plots in
`<output>/scans/scan_<id>/`; `load_scan(1234501)` gives them back as tables.
([Offline: analyse_scan](scans.md#offline-analyse_scan))

### On the HPC: the full-frame reduction

The same analysis for **every pixel** of the chosen panels, as images per step (and per time-tool
bin), to look for signal anywhere on the detector, not only in the ROIs. It reads every shot and
makes GB of images, so it is a pure offline tool for the HPC, run as a **batch job**:

```python
set_ff_panels(7, 8)                          # the panels that see the signal (all: set_ff_panels())
full_frame_job(1234501, tt=True)             # submits the job (cores and memory chosen for the scan)
view_full_frame(1234501)                     # when done: pump on, off and on / off per step or bin
ff = load_full_frame(1234501)                # the images as arrays, also at home (FullFrame("<file>.h5"))
```

Use it on the scans that matter, after `analyse_scan` showed which ones; copy the `.h5` file home
for the detailed work. ([Full-frame reduction](fullframe.md))
