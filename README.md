# SILAS: SACLA Imaging, Logging and Analysis Suite

Online and offline analysis of pump-probe experiments at the **SACLA** X-ray free-electron laser
(beamline BL3), with MPCCD detectors and a timing tool. One Python package (`silas`, in the
folder `SILAS`) used in two places:

| Where | Network | What for |
|---|---|---|
| beamline analysis computers, during the beamtime | DAQ-USER-LAN | **online**: watch the experiment while it runs |
| SACLA HPC, during and after the beamtime | HPC-LAN | **offline**: analyse every shot of the stored runs |

and checked on a laptop before the beamtime, against a local copy of SACLA's Python libraries.

## What it does

- **Session**: one interactive prompt (IPython, or a Jupyter notebook on the HPC) with every
  command; the experiment settings (detectors, aliases, ROIs, corrections) live in one config file
  per place.
- **Run logger**: a table of all runs and of all scans (which runs, which motor, which range),
  online as the runs come in, or rebuilt on the HPC.
- **MPCCD viewer**: the assembled detector live, or the average of stored runs split by pump on /
  off; dark, keV calibration, threshold, histogram, geometry tuning, ROIs drawn on top.
- **Energy calibration, threshold and dark**: everything in keV of deposited energy per pixel.
- **Time tool**: edge finding on the timing camera, background, calibration, a per-shot edge log
  (online and offline), and time-tool binning of the scan analysis.
- **Scan analysis**: ROI signals through a scan, live at the beamline and offline on the HPC
  (every shot, medians with their spread, referenced shot by shot by I0, pump on / off, time-tool
  bins).
- **Full-frame reduction**: the same analysis for every pixel, as images per scan step or time-tool
  bin (HPC batch job), with a viewer.
- **Installers** for the HPC (shortcuts, IPython, Jupyter) and the beamline computers, and a
  local SACLA environment for development with type stubs of the SACLA libraries.

## Documentation

The documentation is in [`docs/src/`](docs/src/) (Markdown, readable here) and builds into an
offline website with search: `python docs/build_docs.py`, then open `docs/html/index.html`.

| Start here | |
|---|---|
| [INSTALL.md](INSTALL.md) | installation: laptop, local SACLA environment, HPC, beamline |
| [Setting up a beamtime, step by step](docs/src/setup-guide.md) | from an empty account to the analysis |
| [Home](docs/src/index.md) · [SACLA basics](docs/src/sacla-basics.md) · [Getting started](docs/src/getting-started.md) | overview, runs and tags, starting the session |

| Where it runs | Setup and calibration | Analysis |
|---|---|---|
| [Working at the beamline](docs/src/beamline.md) | [Configuration](docs/src/configuration.md) | [Scan analysis](docs/src/scans.md) |
| [Working on the HPC](docs/src/hpc.md) | [Detectors and aliases](docs/src/detectors.md) | [Full-frame reduction](docs/src/fullframe.md) |
| [Jupyter notebooks on the HPC](docs/src/jupyter.md) | [Run logger](docs/src/run-logger.md) | |
| [Profiles and modes](docs/src/profiles-and-modes.md) | [Time tool](docs/src/timetool.md) | |
| | [MPCCD viewer](docs/src/mpccd-viewer.md) | |
| | [Energy calibration, threshold and dark](docs/src/energy-calibration.md) | |
| | [Regions of interest](docs/src/rois.md) | |

Development: [Running and writing scripts](docs/src/scripts.md),
[Development notes](docs/src/development.md) (code rules, checks, the local SACLA environment, VS
Code, the docs). The built website also has a reference page per module and the list of all
session commands, generated from the code.

## Requirements

- An account on the SACLA HPC (and, at the beamline, on the analysis computers) and the SACLA VPN.
- At SACLA: nothing else; the code runs with SACLA's Intel Python 3.7 and its libraries.
- On a laptop (development, optional): Linux, Python 3.9 (e.g. via `uv`), bash.

## Not included

SACLA's libraries and data and SACLA's manuals belong to RIKEN / JASRI and are not part of this
repository. With an HPC account, scripts here copy them for your own use:
`sacla_local_api/scripts/fetch_sacla.sh` (libraries, for the local environment) and
`docs/sacla_manuals/build_all.py --download` (manuals, VPN). See [INSTALL.md](INSTALL.md).

## How it was made

The code, scripts and documentation were written with **Claude Opus 5.5** (Anthropic) as a
programming assistant, in conversation with the author. The author ran every check and test 
on a laptop and on real data on the SACLA HPC. Every part was tested by the author before 
it was kept.

## License and citation

MIT, see [LICENSE](LICENSE). If you use it in published work, please cite it as given in
[CITATION.cff](CITATION.cff).
