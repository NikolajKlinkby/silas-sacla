# SILAS: SACLA Imaging, Logging and Analysis Suite

SILAS (the `SILAS` folder, Python package `silas`) is analysis software for pump-probe experiments at the SACLA X-ray
free-electron laser (beamline BL3), with MPCCD detectors and SACLA's arrival-timing monitor. It is one Python package used in two places:

| Where | Network | What it is for |
|---|---|---|
| At the beamline, during the beamtime | **DAQ-USER-LAN** | *Online analysis*: watch the experiment while it runs (run log, live detector data). |
| On the SACLA HPC cluster | **HPC-LAN** | *Offline analysis*: process the stored data of finished runs. |
| On your own computer | – | Develop and check code safely, without SACLA access. |

Everything is used from one interactive command line, the **session**. You start it once,
it sets up the environment for the place you are working (a *profile*), and it gives you
commands for everything the package can do.

```bash
python start_session.py --profile beamline
```

```python
commands()              # list every command
show_detectors()        # what the config knows about
start_online_logger()   # start the run logger in its own terminal
scan_info()             # the newest scan: runs, scanned motor, steps
```

## Where to start

- New to SACLA? Read [SACLA basics](sacla-basics.md) first: runs, tags, detectors and the two networks.
- Then [Getting started](getting-started.md): starting the session on your laptop, at the beamline and on the HPC.
- [Setting up a beamtime, step by step](setup-guide.md): from an empty account to the first analysis (configuration, run log, time tool, energy calibration, ROIs).
- [Working at the beamline](beamline.md): setting up the analysis computers `xu-bl3-anapc01/02`.
- [Working on the HPC](hpc.md): which Python, copying the code, getting a compute node.
- [Jupyter notebooks on the HPC](jupyter.md): the session's commands in a notebook in your
  laptop's browser, with the plots and viewers in the notebook.
- [Profiles and modes](profiles-and-modes.md) explains why the same command runs at the beamline but is only *checked* on a laptop.
- [Configuration](configuration.md) and [Detectors and aliases](detectors.md) describe what the session remembers.
- [Run logger](run-logger.md) is the first analysis tool: the table of all runs and of all
  [scans](run-logger.md#scans) (which runs, which motor, which range).
- [MPCCD viewer](mpccd-viewer.md) shows the octal MPCCD live, or on the HPC the average of
  stored runs (pump on / off); [Energy calibration, threshold
  and dark](energy-calibration.md) says what to do **first at every beamtime** (dark, photon
  energy, threshold) so that its keV values are right.
- [ROIs](rois.md): the regions of the detector that the scan analysis follows.
- [Scan analysis](scans.md): the ROI signal through a scan, pump on / off and their ratio,
  live at the beamline (`start_scan_viewer`) and from the stored data on the HPC (`analyse_scan`).
- [Full-frame reduction](fullframe.md): the scan analysis for every pixel, as images per step
  and time-tool bin (HPC batch job), to take home.
- [Time tool](timetool.md): the arrival-timing camera shot by shot, its ROI and trace, the
  background (live and offline); the edge finding comes next.
- [Session commands](commands.html) lists every command with its arguments.

## How the code is organised

```
SILAS/
├── start_session.py     start the interactive session
├── config/              profiles.json + one config file per profile (created automatically)
├── output/<profile>/    results: runlog.csv, scans.csv, motors.csv, ...
├── silas/
│   ├── core/            shared by online and offline: config, SACLA access, run log, ...
│   ├── online/          scripts for the beamline (DAQ-USER-LAN)
│   ├── offline/         scripts for the HPC (HPC-LAN)
│   └── cli/             the interactive session and its commands
├── scripts/             small example scripts
├── tests/               tests that run without SACLA libraries
└── docs/                this documentation (src/ = Markdown, html/ = built pages)
```

Functions that work the same online and offline live in `core`. The `online` and `offline`
folders only hold what is specific to one place, usually a thin script around `core`.
