# SACLA basics

This page explains the SACLA terms used everywhere in SILAS. No prior knowledge of
SACLA is assumed.

## Beamline

SACLA delivers X-ray pulses to several beamlines. SILAS was written for **BL3**, so the
config uses `beamline: 3` by default (`cfg.set("beamline", 2)` for another one). Most database functions need the beamline number.

## Tags: one number per X-ray pulse

Every X-ray pulse gets a **tag number**. Everything recorded for that pulse (detector
images, photodiode readings, motor positions) is stored under the same tag, which is how
data from different instruments is matched.

- The tag counter runs at a fixed clock. At 30 Hz operation consecutive pulses therefore do
  not have consecutive tags (typically they differ by 2), so never assume `tag + 1` exists.
- A tag is stored as two parts: the **high tag** (changes rarely) and the low **tag**. Most
  database queries need both. SILAS always takes the high tag from the run, so you
  normally only deal with the low tag.

## Runs

A **run** is one continuous measurement started and stopped at the DAQ (data acquisition)
console. Each run has

- a **run number** (counts up during the beamtime),
- a **status**: `running`, `paused` or `stopped` (only stopped runs are complete),
- a start and stop time, a first and last tag, the number of tags,
- a **comment** typed by the operator (often the most useful information: sample, scan, ...),
- the list of image detectors that recorded data.

The [run logger](run-logger.md) collects this for every run of the beamtime.

## Scans

A **scan** moves one motor (e.g. the delay stage) through a list of positions and measures
at each. SACLA's scan tool makes **one run per scan step**: the motor moves, a run is
recorded at that position, the motor moves on, the next run starts. So a 51-step delay scan
is 51 consecutive runs, and during each run the motor stands still.

For every run of a scan, `dbpy` knows the step number and the total number of steps
(`read_run_supplementinfo`), but **not which motor was scanned**. SILAS finds the
motor by comparing the motor positions of the steps, and keeps a table of all scans: see
[Scans](run-logger.md#scans) in the run logger.

Things that happen in real scans:

- a step is **repeated** (two runs with the same step number), e.g. after bad data;
- a scan is **stopped early**, so the last steps are missing;
- plain runs (e.g. a dark run) can lie between scans.

## Two kinds of data

**Image detectors** produce a 2-D picture per tag. At SACLA these are mainly **MPCCD**
detectors (each sensor 1024 × 512 pixels of 50 µm) and other cameras. Each detector has an
ID such as `MPCCD-2N0-M02-001`. A multi-sensor MPCCD (e.g. an octal detector with 8
sensors) appears as one ID per sensor.

**Point detectors** (SACLA calls them *equipment*) produce one number per tag: photodiodes
measuring the pulse intensity, beam status flags, motor positions, spectrometer values.
Their names describe where they are and what they measure, for example

| Name | Meaning (roughly) |
|---|---|
| `xfel_bl_3_tc_bm_1_pd/charge` | BL3, beam monitor 1 photodiode, charge (pulse intensity) |
| `xfel_bl_3_shutter_1_open_valid/status` | BL3 shutter 1 open (1) or closed (0) |
| `xfel_bl_3_st_5_motor_user_1/position` | BL3, experimental station 5, user motor 1 position |
| `xfel_mon_bpm_bl3_0_3_beamstatus/summary` | beam status from a beam position monitor |

These names are long, so SILAS lets you give them short [aliases](detectors.md) such
as `i0` or `time_delay`.

The values of point detectors are stored per tag in the **SyncDB** (synchronous database).

## The two networks

| | DAQ-USER-LAN (beamline) | HPC-LAN (SACLA HPC cluster) |
|---|---|---|
| When | during the beamtime | during and after the beamtime |
| Image data | live, only the most recent tags (they disappear quickly) | everything that was stored |
| Point detectors / run info | yes | yes |
| Typical use | quick look, run log, live monitoring | full processing of runs |
| Python library for images | `olpy` (OnlineUserAPI) | `stpy` (DataAccessUserAPI) |

On the HPC the front-end (login) node is only for editing and submitting jobs. The SACLA
data libraries do not work there; analysis runs on compute nodes through the PBS queue
system (`qsub`).

## SACLA Python libraries

| Module | Purpose | Online | Offline |
|---|---|---|---|
| `dbpy` | run information, tags, point detector values (SyncDB) | ✓ | ✓ |
| `olpy` | live images from detectors | ✓ | |
| `stpy` | stored images of a run | | ✓ |
| `ippy` | image processing on numpy arrays (ROI, binning, radial profile, ...) | ✓ | ✓ |

They are compiled C libraries for **Python 3.7** (Intel Python) on SACLA computers. Two
rules catch almost everybody:

- arguments are **positional only**: `dbpy.read_runinfo(3, run)` works, `read_runinfo(bl=3, run=run)` does not;
- lists of tags must be **tuples**, not lists.

SILAS wraps the functions we need (see `silas.core.db`) so these rules are handled
in one place.

## SACLA's own documentation

A local copy of SACLA's software manuals is in `docs/sacla_manuals/` (built with these docs; see
[Development notes](development.md#the-sacla-manuals)). Start at the
[software overview](../sacla_manuals/html/SACLA_Software.html); the manuals behind the libraries
SILAS uses:

| Library | What | Manual |
|---|---|---|
| `dbpy`, `stpy` | runs, tags, point detectors (SyncDB); stored detector images | [DataAccessUserAPI, Python](../sacla_manuals/resources/manuals/Offline/DataAccessUserAPIPythonIF_UsersManual_en.pdf) |
| `olpy` | detector images now (online) | [OnlineUserAPI, Python](../sacla_manuals/resources/manuals/Online/OnlineUserAPIPythonIF_UsersManual_en.pdf) |
| `ippy` | image processing (e.g. `reconstruction`) | [ImageProcessingUserAPI, Python](../sacla_manuals/resources/manuals/Offline/ImageProcessingUserAPIPythonIF_UsersManual_en.pdf) |
| MPCCD geometry | how the octal MPCCD panels are put together | [MPCCD octal image assembly algorithm](../sacla_manuals/resources/manuals/Offline/MPCCD_octal_image_assembly_algorithm_ver1.1.pdf) |
| HPC | modules, queues, file systems | [System pages](../sacla_manuals/html/SACLA_System.html) |

The links work in the built documentation when the manuals were downloaded on this computer
(`python docs/sacla_manuals/build_all.py --download`, VPN on); the manuals are not part of the
repository.
