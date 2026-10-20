# Configuration

Each profile has its own configuration file, `config/<profile>.json` (for example
`config/beamline.json`). The beamline computer and the HPC therefore each keep their own
settings and their own output. If there is none, the first session makes a rough default (no
detectors, output in SILAS's `output/<profile>/`); set it up properly with `setup_config`.

## Setting it up

In the session, or from bash (the same questions and options):

```python
setup_config()                    # the rough default
setup_config(beamline=3)          # the standard setup of BL3
setup_config(beamline=3, first_run=1234400, photon_energy_keV=9.9, output="pcmo_2026")
```

```bash
bash scripts/setup_config.sh                    # the rough default
bash scripts/setup_config.sh --beamline 3       # BL3
bash scripts/setup_config.sh --beamline 3 --first-run 1234400 --photon-energy 9.9 --output pcmo_2026
bash scripts/setup_config.sh --profile beamline --beamline 3   # another profile's config
```

What is not given is asked for: the first run (empty: none, set it later), the photon energy in keV
(empty: from the run record of each run) and the output folder, a name under `/UserData/<user>/`
(empty: `tmp`) or a full path. `yes=True` / `--yes` asks nothing and takes the defaults. An
existing config is replaced only after asking (`--yes`: without asking); the old one is kept as
`config/<profile>.json.<time>.bak`.

| | rough default | `beamline=3` |
|---|---|---|
| beamline, first run, photon energy, output | ✓ | ✓ |
| `calibration.ev_per_absgain_adu` = 3.65 (for MPCCD arrays) | ✓ | ✓ |
| point detectors | – | `optical_shutter`, `laser_shutter`, `i0_1`, `i0_2`, `delay_m`, `delay_tt` |
| MPCCD arrays | – | `octal` (MPCCD-8N0-3-002, the default array) and `dual` (MPCCD-2B1-M03-001: 30 Hz, threshold 2 keV) |
| scan settings | – | pump `optical_shutter` = 1, references `i0_1`, `i0_2`, sparse bins below 30 shots hidden |

The standard setup is in `silas/core/standard.py` (only BL3 so far). ROIs, darks, the time-tool
background and calibration are results of the experiment and are made in the session as usual.

## What is in it

```json
{
  "version": 2,
  "profile": "beamline",
  "beamline": 3,
  "first_run": 1234400,
  "photon_energy_keV": null,
  "paths": {
    "output": "/home/.../SILAS/output/beamline"
  },
  "detectors": {
    "MPCCD-8-2-001": {
      "kind": "MPCCD-array", "alias": "octal",
      "panels": ["MPCCD-8-2-001-1", "...", "MPCCD-8-2-001-8"],
      "geometry": {"manipulator_shift_um": 0.0, "panel_offsets": {}},
      "calibration": {"ev_per_absgain_adu": 3.65},
      "corrections": {"dark": true, "dark_file": null,
                      "threshold": true, "threshold_keV": 1.0},
      "display": {"units": "keV", "mode": "average", "cmap": "viridis", "log": false},
      "rois": {"bragg_002": {"rows": [290, 330], "cols": [180, 225]}}
    }
  },
  "point_detectors": {
    "xfel_bl_3_tc_bm_1_pd/charge": {"alias": "i0"},
    "xfel_bl_3_st_5_motor_user_1/position": {"alias": "time_delay", "unit": "pulse"}
  }
}
```

| Key | Meaning |
|---|---|
| `beamline` | beamline number, 3 by default |
| `first_run` | first run of the beamtime; the [run logger](run-logger.md) starts here |
| `photon_energy_keV` | photon energy for all analysis; `null`: from the run record ([Energy calibration](energy-calibration.md#the-photon-energy)) |
| `paths.output` | folder for results of this profile: `runlog.csv`, `scans.csv`, `motors.csv`, ... |
| `detectors` | image detectors and MPCCD arrays, see [Detectors and aliases](detectors.md) and [MPCCD viewer](mpccd-viewer.md); an array's `calibration` and `corrections` (dark, threshold in keV): [Energy calibration, threshold and dark](energy-calibration.md); its `rois`: [ROIs](rois.md) |
| `point_detectors` | point detectors we use, see [Detectors and aliases](detectors.md). Those with `motor` in the name are the "configured motors" of the [scan table](run-logger.md#scans) |
| `scan.search` | optional, default `"all"`: search all BL motors for the scanned motor; `"config"`: only the configured ones ([Settings](run-logger.md#settings)) |
| `scan.n_sample` | optional, default `5`: tags per run read for a motor snapshot |
| `scan.pump_detector`, `scan.pump_on_value` | optional, default `optical_shutter` and `1`: which point detector says "pump on", and its value (`set_pump`; [Scan analysis](scans.md#before-a-scan)) |
| `default_mpccd` | optional: the MPCCD array that commands use when none is named (`use_mpccd`, also set by `start_mpccd_viewer(name=...)`); default: the first array ([MPCCD viewer](mpccd-viewer.md#which-array-the-commands-use)) |
| `scan.references` | optional, default none: reference point detectors (e.g. i0) of the scan analysis, a name or a list (`set_references`; [Scan analysis](scans.md#before-a-scan)) |
| `paths.cache` | optional: folder for the kept caches of the offline MPCCD view; default `/work/<user>/silas_cache` ([MPCCD viewer](mpccd-viewer.md#offline-viewer-hpc)) |
| `scan.tt_binning`, `scan.tt_bin_ps`, `scan.sparse_hide`, `scan.sparse_min` | optional, default `false`, `0.05`, `true`, `20`: time-tool binning of the scan analysis and its sparse bins (`set_tt_bins`; [Scan analysis](scans.md#time-tool-binning)) |
| `timetool` | optional: the time tool's camera, ROI, binning, laser detector and background ([Time tool](timetool.md#settings-in-the-config)) |

More sections will be added as the analysis grows.

Settings that no longer exist are removed automatically: an MPCCD array's old
`corrections.gain` and `corrections.threshold_adu` (the threshold is now
`corrections.threshold_keV`) are dropped, with a message, the first time the array is read.

## Reading and changing it in the session

The session has the configuration as the object `cfg`. Keys are written with dots for nested
sections:

```python
cfg.show()                          # everything
cfg.show("point_detectors")         # one section
cfg.get("beamline")                 # -> 3
cfg.get("paths.output")
cfg.set("first_run", 1234400)       # saved to the file immediately
cfg.set("paths.output", "/work/me/my_experiment")
cfg.delete("some.key")
cfg.reload()                        # re-read the file (if someone else changed it)
```

For detectors and point detectors use the dedicated commands (`add_point_detector`,
`set_alias`, ...): they check that aliases are unique.

## How scripts see the configuration

`cfg` also notices when another program (the viewer, a script, a second session)
changed the file and re-reads it before reading or writing, so changes made elsewhere are
never overwritten.

Every change is written to the file at once, and safely: the file is written under a
temporary name and then renamed, so another program never reads a half-written file.

A script started from the session (for example the online logger in its own terminal) gets
the profile and the file name from the session and reads the file itself when it starts. It
therefore uses exactly the configuration you had when you started it. Changes made later in
the session are seen by scripts started later.

You can also edit the file with a text editor; run `cfg.reload()` in an open session
afterwards.
