# Energy calibration, threshold and dark

MPCCD images are read in ADU (the detector's raw units). Everything we compute from them,
from the live histogram to the ROI analysis, works in **deposited energy (keV)** instead.
This page explains how ADU become keV, where the photon energy comes from, how the one
lower threshold works, and why a **dark** has to be taken before any of it can be
trusted.

**In short, at the start of a beamtime:**

```python
capture_dark(shots=300)      # 1. X-ray shutter CLOSED: the dark (every user, see below)
set_photon_energy(9.9)       # 2. or set_photon_energy(None): use the run record
energy_info()                # 3. check photon energy, calibration and threshold
start_mpccd_viewer()         # 4. X-rays on, press a for single shots: 1-photon peak at the photon energy?
set_threshold(1.0)           # 5. the lower threshold in keV, used by all analysis
```

## Why keV and not photons

One X-ray photon makes a cloud of charge in the sensor. If it lands near a pixel edge,
the charge is shared between two or more neighbouring pixels. So a single pixel does
**not** hold a whole number of photons: it holds a part of one photon, a whole photon,
or several. What a pixel does measure reliably is the **energy deposited** in it.
That is why the viewer shows keV, and why the threshold is set in keV.

The photon energy is still needed: to mark where 1, 2, 3 photons lie in the histogram,
and later to turn summed energy into a number of photons (summed over a region, the
charge sharing cancels out).

## From ADU to keV

SACLA stores with every image the gain of each panel, `mp_absgain`, in **electrons per
ADU**. Creating one electron-hole pair in silicon takes about 3.65 eV, so

```
keV per ADU of panel p  =  mp_absgain(p) × ev_per_absgain_adu / 1000
```

`ev_per_absgain_adu` (nominally 3.65 eV) is the one calibration number, a setting of each
MPCCD array (`calibration.ev_per_absgain_adu`). Each panel gets its own conversion from its
own `mp_absgain`, so the panels need no separate gain normalisation.

The number can be measured from stored data with `tests/adu_histogram.py` (see
[below](#measuring-the-calibration-from-stored-data)): the 1-photon peak in ADU of each panel,
from single pixels, 3×3 clusters and the 2-photon peak, gives eV per (ADU × absgain). Measured
values on MPCCD data with 9.9 keV photons are typically 3.63 – 3.69, so 3.65 eV is right to
about 1 %. Fine-tune it at the beamtime with the histogram
([Checking it](#checking-it-with-the-histogram)):

```python
set_energy_calibration(3.65)          # the default
```

## The photon energy

```python
set_photon_energy(9.9)       # keV, used by all analysis
set_photon_energy(None)      # take it from the run record
energy_info()                # what is used now, and where it comes from
```

The config key `photon_energy_keV` (at the top level of the [configuration](configuration.md))
decides:

- **set** (a number): that value is used, and the run record is not read at all;
- **null** (the default): the photon energy recorded by SACLA for the run
  (`dbpy.read_config_photonenergy_in_keV`), read once per run.

Set it when the run record is wrong or not available (e.g. on a laptop), or when the
photon energy is changed during the beamtime and you want to be sure which value is used.
Remember to change or clear it when the energy changes!

## The threshold

There is **one lower threshold**, in keV of deposited energy, per MPCCD array
(`corrections.threshold_keV`, switched on and off with `corrections.threshold`). Pixels
below it are set to 0 in every shot, before anything is averaged or summed. It removes
the readout noise, so that what remains is signal. This number is used by **all**
analysis (viewer, ROI sums, offline), so it is set in one place:

```python
set_threshold(1.0)           # 1.0 keV and switch it on
set_threshold(on=False)      # switch it off, keep the value
```

or in the viewer with `t` (on/off) and `+` / `-` (× 1.25 / ÷ 1.25); both write the
configuration, and every running program follows within a second.

**How it is applied.** The keV threshold is converted once into ADU for every panel
(`threshold_keV / keV-per-ADU`), and each shot is compared with that number as it comes
from the detector (after the dark is subtracted). The images are never rescaled for the
threshold; conversion to keV happens only where keV are needed (e.g. once per display
update in the viewer).

**Choosing the value.** The noise of an MPCCD pixel is about 0.15 keV (σ ≈ 10 ADU), so
anything from about 1 keV removes the noise. A higher threshold also removes small shares
of shared photons and, if wanted, fluorescence of the sample or substrate (e.g. Ti Kα at
4.5 keV next to 9.9 keV photons; the histogram shows such lines). Half the photon energy
is the classic choice for counting photons; for summing energy a low threshold (≈ 1 keV)
loses less signal.

## The dark: do this first

Every pixel has an offset, its **pedestal**: the value it reads without any X-rays. The
dark is the average of many shots without X-rays, and it is subtracted from every shot.
**Without a correct dark, the keV values are wrong**: the whole histogram is shifted by the
pedestal, the threshold cuts at the wrong place, and photon peaks are not at the photon
energy. (With the test data the 100 ADU pedestal looks like 6.6 keV in every pixel.)

```python
capture_dark(shots=300)      # with the X-ray shutter CLOSED
```

averages 300 shots, saves them to `<output>/darks/dark_<array>_<time>.npz`, sets the file
as the array's `dark_file` and switches dark subtraction on. A running viewer picks it up
immediately.

**On the HPC**, from a stored run recorded with the X-rays blocked (a dark run):

```python
set_dark(run=1234470)                     # all shots of the run (6 processes)
set_dark(run=1234470, shots="xray_off")   # only shots with the X-ray shutter closed
set_dark(run=1234470, max_shots=500)      # at most 500 shots, spread over the run
set_dark(file="/path/to/dark_....npz")    # a saved dark file
```

It averages the raw shots (all panels of the array), saves them to
`<output>/darks/dark_<array>_run<N>_<time>.npz` (the same kind of file as `capture_dark`), and sets
the file as the array's `dark_file` with dark subtraction on. `shots="all"` (default) takes every
shot, as for the time-tool background: the X-rays can be blocked by something the flags do not
show, so look at the run first (`start_mpccd_viewer(run=...)`: no photons).

**When.**

- **At the start of every beamtime, for every user.** The dark file is stored in your
  output folder and set in your [configuration](configuration.md), so everyone who runs
  the viewer or the analysis with their own copy or profile needs a dark. Either capture
  one, or use a colleague's file:
  `set_mpccd("corrections.dark_file", "/path/to/dark_....npz")`.
- **Again** after the detector was restarted or reconfigured, when its temperature
  changed, or when the noise peak in the histogram (X-rays off) is no longer at 0.

**Checking it.** Close the X-ray shutter, press `a` in the viewer for single shots: the
histogram must be one narrow peak centred on 0 keV. If it is not centred on 0, take a new
dark.

Stored data read offline (stpy) can already be offset-corrected by SACLA; check this with the
histogram of a run with the X-ray shutter closed. Online a dark is taken anyway: on corrected
data it is close to zero and removes what is left of the offset.

## Checking it with the histogram

Below the image, the [MPCCD viewer](mpccd-viewer.md) shows a histogram of the deposited
energy per pixel (log scale):

- it is always made from **single shots**: the newest shot, or, in average mode, all
  single shots shown since the last reset added up (a histogram of the averaged image
  would show no photon peaks);
- red line: the threshold; green dotted lines: 1, 2, 3 × the photon energy;
- the spike at 0 are the pixels set to 0 by the threshold, the flat part between the
  threshold and the 1-photon peak are pixels with part of a shared photon.

With X-rays on, the 1-photon peak must sit on the first green line. If it sits at P keV
instead of the photon energy E, correct the calibration:

```python
set_energy_calibration(3.65 * E / P)      # new = old × E / P
```

`u` switches the viewer between keV and raw ADU (each panel in its own ADU, not calibrated);
the status line shows the photon energy and its source, and how many ADU one photon is on
each panel.

## Measuring the calibration from stored data

`tests/adu_histogram.py` measures ADU per photon from finished runs on the HPC (read only):

```bash
bt_node small
bt_env; cd ~/SILAS
python tests/adu_histogram.py 1234501 1234526 1234551 --det MPCCD-8N0-3-002   # runs with photons
python tests/adu_histogram.py 1234567 1234568 --det MPCCD-8N0-3-002 --tags 300
```

For every panel it makes histograms of the single pixels and of 3×3 clusters around local
maxima (which collect the whole charge of a shared photon), finds the noise and the 1- and
2-photon peaks, and prints ADU per photon, eV per ADU and eV per (ADU × absgain), the
number for `set_energy_calibration`. Plots and numbers are written to
`output/adu_calib/`.

## Commands

| Command | What it does |
|---|---|
| `energy_info()` | photon energy and its source, calibration, threshold (in keV and photons) |
| `set_photon_energy(keV)` | photon energy for all analysis; `None`: from the run record |
| `set_threshold(keV, on=True)` | the lower threshold; `set_threshold(on=False)` switches it off |
| `set_energy_calibration(ev)` | `ev_per_absgain_adu` of an array (default 3.65) |
| `capture_dark(shots=300)` | take a dark (X-ray shutter closed) and use it |
| `set_dark(run=N, shots="all", max_shots=None)`, `set_dark(file=...)` | a dark from a stored run (HPC), or a saved dark file, and use it |
