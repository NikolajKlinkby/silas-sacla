# Run logger

The run logger keeps a table of every run of the beamtime: when it ran, how long, how many
tags, the operator's comment and which image detectors recorded data. It also keeps the
**scan table**: which runs belong to which scan, which motor was scanned and over which
range (see [Scans](#scans) below). It only uses `dbpy`, which works on both networks, so
the same code serves online and offline.

| | Online logger | Offline logger |
|---|---|---|
| Where | beamline (DAQ-USER-LAN) | HPC (HPC-LAN) |
| Behaviour | catches up from the first run, then **keeps watching** for new runs | catches up from the first run (or **recreates** the log), then stops |
| Started with | `start_online_logger()` (opens its own terminal) | `make_runlog()` (runs in the session's terminal) |
| Stop | Ctrl-C in its terminal | stops by itself |

## Files

All files are in the profile's output folder (`paths.output`, e.g. `output/beamline/`):

| File | Content |
|---|---|
| `runlog.csv` | one row per run (below) |
| `scans.csv` | one row per scan: runs, scanned motor, steps (see [Scans](#scans)) |
| `motors.csv` | the motor snapshot of every run, from which the scan table is made |
| `runlog.csv.lock` | exists while a logger is writing (see *Lock file*) |

`scans.csv` is rebuilt completely from `runlog.csv` and `motors.csv` whenever a run changes,
so it can always be deleted; the next logger pass writes it again.

## Before the first start

Set the first run of the beamtime once:

```python
cfg.set("first_run", 1234400)
```

## During the beamtime

1. Start the online logger once at the beginning: `start_online_logger()`. Leave its terminal open.
2. Work in the session as usual. After (or during) a scan:
   `scan_info()` summarises the newest scan, `scan_range()` gives its runs.
3. Paste the output of `scan_info()` to whoever analyses the scan on another computer
   (e.g. on the HPC, where `make_runlog()` gives the same tables).

## Online

```python
start_online_logger()           # checks for new runs every 5 s
start_online_logger(poll=2)     # every 2 s
```

A new terminal opens and prints one line per run as it is logged, and one line about the
scan a run belongs to:

```
run log: .../output/beamline/runlog.csv  (beamline 3, following new runs, Ctrl-C to stop)
scans:   .../output/beamline/scans.csv
scan detection: 212 motors searched at the start of a scan (5 configured)
run 1234400   stopped  2026-11-02 09:14:03  tags=1800                  dark run
run 1561458   stopped  2026-11-02 09:20:41  tags=2002    step 1/51    time scan of (002) | ND -14060
    scan 1561458: runs 1561458, 1 of 51 steps, incomplete, motor not known yet
run 1561459   running  2026-11-02 09:23:10  tags=1200    step 2/51    time scan of (002) | ND -14060
    scan 1561458: runs 1561458-1561459, 2 of 51 steps, running, delay_m 1100 -> 1134 (step 34)
```

The session itself stays free for other work. Only one logger can write a given log: a
second one refuses to start (see *Lock file* below).

## Offline

```python
make_runlog()                   # add the runs that are missing from the log
make_runlog(rebuild=True)       # throw the log away and recreate it from first_run
make_runlog(last_run=1561500)   # stop at a given run
```

or from a shell (for example in a PBS job):

```bash
python -m silas.offline.logger --rebuild
python -m silas.offline.logger --help
```

Example, the run log of an earlier beamtime on the HPC (stored data stay readable there for
three years):

```python
cfg.set("first_run", 1234470)          # the first run of that beamtime
make_runlog(rebuild=True, last_run=1234999)
scan_info()
```

`make_runlog` reads the runs with **6 worker processes** (`workers=`, default 6, at most the
cores of the job; `workers=1`: one run after the other). First the records of all runs are
read in parallel, then the motor snapshots with whole scans per worker: inside a scan the
steps stay in order, because step 1 searches all motors and the later steps use what it
found. The online logger reads one run after the other (it follows the beamline anyway).

**Starting again.** A logger that is stopped in any way (Ctrl-C, a crash, a lost connection,
the end of a batch job) can simply be started again: it continues after the last run in the log
and re-checks the runs that were not finished yet. Nothing is rebuilt unless you ask for it
(`rebuild=True`). The logger saves the log every 20 runs, so a hard stop loses at most the last
20 runs, which are then read again.

### As a batch job (HPC)

A whole beamtime takes hours. Run it as a PBS job, which needs neither a terminal nor the
laptop's connection (24 h, 6 cores and 6 workers, 16 GB):

```bash
qsub -v LAST_RUN=1234999 ~/SILAS/scripts/runlog_job.sh             # continue up to this run
qsub -v LAST_RUN=1234999,REBUILD=1 ~/SILAS/scripts/runlog_job.sh   # start again from first_run
qstat -u $USER
tail -f ~/SILAS/output/runlog_job.log
```

`first_run` comes from the hpc config; without `LAST_RUN` it goes up to the newest run.

A run log made before the scan table existed is upgraded automatically: on the first pass
every run is read once more (to take its motor snapshot). This is slow only for the first
two steps of each scan (see [How the logger finds the scanned motor](#how-the-logger-finds-the-scanned-motor)).

## Looking at the log

```python
show_runlog()            # last 20 runs (a pandas table)
show_runlog(None)        # all runs
log = show_runlog(None)
log[log.comment.str.contains("dark")]
```

`show_runlog` only reads the file, so it also works on a laptop with a copied log.
The same holds for the scan commands (`show_scans()`, `scan_info()`, ...; see
[Looking up scans in the session](#looking-up-scans-in-the-session)).

## The log file

`<output>/runlog.csv`, one row per run, index column `run`:

| Column | Content |
|---|---|
| `status` | `running`, `paused` or `stopped` |
| `start_jst`, `stop_jst` | start / stop time, Japan time, readable |
| `duration_s` | run length in seconds (empty while running) |
| `total_tagnumber` | number of tags (pulses) in the run |
| `scan_id` | the scan the run belongs to (= its first run); empty for a plain run |
| `step`, `total_steps` | scan step of this run and number of steps of the scan (SACLA's numbering) |
| `scan_motor`, `scan_value` | the scanned motor (true name) and its value in this run |
| `comment` | operator comment |
| `runtype` | run type from the DAQ |
| `scanmode` | SACLA's scan mode (e.g. `sync`) |
| `moving` | motors that moved *during* the run (normally empty) |
| `detectors` | image detectors with data in the run, separated by `;` |
| `start_tagnumber`, `end_tagnumber`, `hightagnumber` | tag range and high tag |
| `stationnumber`, `fy_id` | experimental station, proposal ID |
| `runstatus` | status as a number (0 stopped, 1 paused, 2 running) |
| `starttime`, `stoptime` | start / stop as Unix time |
| `logged_at` | when the row was last written (Japan time) |

## How it works

- It starts at `first_run`, or right after the last run already in the log.
- Every few seconds it asks for the newest run number of the beamline and logs every run up
  to it. Run numbers that do not belong to our beamline are skipped.
- A run is written as soon as it exists, also while it is still running. Rows of running or
  paused runs are checked again on every pass and updated until the run has stopped.
- The step information (`step`, `total_steps`, `scanmode`) is read with every run. The motor
  snapshot is taken when the run appears and once more when it has stopped, not on every
  pass while it runs.
- After every change the scan table is rebuilt and the scan columns of the run log
  (`scan_id`, `scan_motor`, `scan_value`) are filled in.
- The file is rewritten as a whole each time something changed, safely (written under a
  temporary name, then renamed), and made group-writable so colleagues can use it.

### Lock file

While a logger runs it keeps `runlog.csv.lock` next to the log, containing the computer
name and process number. A second logger on the same log refuses to start. The lock is
removed when the logger stops. A lock left behind by a logger that was killed is taken over by
the next one: on the same computer when its process is gone, and from another computer (e.g. a
batch job on another node) when its heartbeat is older than 10 minutes. A message says so.

## Scans

### How SACLA records a scan

SACLA's scan tool makes **one run per scan step** (see also [SACLA basics](sacla-basics.md#scans)).
For each run, `dbpy.read_run_supplementinfo` gives the step number, the total number of
steps, the run's tag range and the scan mode (e.g. `sync`); its `comment` is usually empty.
It does **not name the motor**, and neither do the run's conditions (`read_run_conditioninfo`:
`current_dac`, `interval`, `chager`, `position`, the same in every step) nor the device
information. The scanned motor stays constant *within* a run; it only changes between runs.
Within a step only per-shot signals change (e.g. the I0 photodiodes and the pump shutter).
A time scan of 51 steps looks like this (example numbers):

| run | step | delay_m |
|---|---|---|
| 1234501 | 1 / 51 | 1100 |
| 1234502 | 2 / 51 | 1134 |
| … | … | … |
| 1234544 | 44 / 51 | 2562 |
| 1234545 | 44 / 51 | 2562 (step repeated) |
| 1234551 | 50 / 51 | 2766 (scan stopped before step 51) |

All other motors stay constant over the scan and end up in the scan's `conditions`.

### How the logger finds the scanned motor

Only point detectors with `motor` and `position` in the name are taken to be scannable.
For every run the logger takes a **motor snapshot**: motor values at 5 tags spread over the
run, kept in `<output>/motors.csv`. Reading all motors of the beamline is slow, so the full
search is only done until the scanned motor is known:

| Run | Motors read |
|---|---|
| step 1 of a scan | all BL3 motors (`xfel_bl_3_*motor*position*`) and the configured motors |
| step 2 | the motors that had data in step 1 |
| later steps | the configured motors and the scanned motor |
| plain run | the configured motors |

A run is read once when it appears and once more when it has stopped. The scanned motor is
the one that

- is constant within every run of the scan, and
- has different values in different steps.

It is therefore known from the second step on; the first run's `scan_value` is filled in
then. Motors from the config (with aliases) are preferred; if more motors change together,
the others are listed in `other_scanned`. If step 2 shows no change (e.g. the scan repeats a
position), the search goes on with the following steps. A motor that changes *within* runs
(an encoder that drifts, a noisy reading) is never taken as the scan motor; it appears in
the run log's `moving` column instead.

`motors.csv` has one row per run: a column per motor (true name), `_moving` (motors that
moved within the run) and `_full` (1 if all BL3 motors were searched for this run).

### How runs are grouped into scans

- A run with step information belongs to a scan; a run without it is a plain run.
- A new scan starts at step 1 or when the total number of steps changes. (A repeated step 1
  right at the start stays in the same scan.)
- Plain runs in between (e.g. a dark run) do not end a scan.
- **Repeated steps stay in the scan**, both runs. Which of them to use is decided later, when
  the MPCCD data are checked for bad data.

### The scan table

`<output>/scans.csv`, one row per scan, index column `scan_id` (= the first run):

| Column | Content | Example |
|---|---|---|
| `first_run`, `last_run`, `n_runs`, `runs` | the runs of the scan | 1234501, 1234551, 51, `1234501-1234551` |
| `total_steps`, `steps_done` | steps planned / different steps recorded | 51, 50 |
| `status` | `running`, `complete` (all steps recorded) or `incomplete` | incomplete |
| `scan_motor`, `scan_motor_alias`, `unit` | the scanned motor | `xfel_bl_3_st_2_motor_1/position`, delay_m, pulse |
| `start_value`, `end_value`, `step_size` | motor value at the first and last step, value change per step | 1100, 2766, 34 |
| `repeated_steps` | steps recorded more than once, with their runs | `44 (1234544, 1234545)` |
| `missing_steps` | steps not recorded | `51` |
| `other_scanned` | further motors that changed with the step | |
| `conditions` | configured motors that stayed constant during the scan | `delay_tt=99340; fluence_1=0; fluence_2=-14060; delay_t12=15` |
| `scanmode`, `start_jst`, `stop_jst`, `n_tags`, `comment` | scan mode, start of the first run, end of the last run, tags in total, comment of the first run | |

Online, a scan between two steps (last run stopped, next not started) shows as
`incomplete` until the next step arrives. The motor of a new scan shows as "not known yet"
until its second step has started.

### Looking up scans in the session

These commands only read the logger's files, fresh at every call. So at the beamline they
follow the online logger running in its own terminal, and they also work on a laptop with
copied files.

Which scan: `-1` the newest (default), `-2` the one before, …; `0` the first scan of the
beamtime, `1` the second, …; or **any run number** of a scan.

```python
show_scans()                 # main columns of the last 20 scans
show_scans(None, full=True)  # all scans, all columns
scan_info()                  # summary of the newest scan (printed), returned as a dict
scan_info(-2)                # the scan before
scan_range()                 # (first run, last run) of the newest scan
scan_run_list(1234518)       # all runs of the scan that run 1234518 belongs to
scan_motor()                 # (alias, true name) of the scanned motor
scan_runs()                  # the runs of the scan: step, motor value, status, tags
scan_overview(1234501, 1234551)   # straight from dbpy, without the log (one row per run)
```

`scan_overview` reads dbpy directly (needs SACLA) and only checks the configured motors; it
is useful for runs that are not in the log, e.g. from another beamtime.

`scan_info()` prints a block that can be pasted into a message for someone analysing on
another computer:

```
scan 1234501   (incomplete, 50 of 51 steps)
  runs       1234501-1234551  (51 runs)
  motor      delay_m (xfel_bl_3_st_2_motor_1/position) [pulse]
  values     1100 -> 2766, step 34
  repeated   44 (1234544, 1234545)
  missing    steps 51
  conditions delay_tt=99340; fluence_1=0; fluence_2=-14060; delay_t12=15
  time       2026-11-02 04:40:55 - 2026-11-02 05:11:30 JST
  comment    time scan of the (002) reflection | 1 in 10 shots off
```

and `scan_range()` prints the runs also as `np.arange(1234501, 1234552, 1)`. If plain runs
lie between the steps of a scan, it says so; `scan_run_list()` then gives exactly the
scan's runs.

### Settings

| Key | Default | Meaning |
|---|---|---|
| `scan.search` | `"all"` | `"all"`: all motors of the beamline are searched at step 1 of a scan; `"config"`: only the configured motors |
| `scan.n_sample` | `5` | tags per run read for the snapshot |

```python
cfg.set("scan.search", "config")
```
