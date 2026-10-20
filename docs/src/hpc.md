# Working on the HPC

The SACLA HPC is where offline analysis runs: all stored detector images and point
detector values of a run can be read there. This page describes the setup used by
SILAS on the SACLA HPC.

## The machines

| Machine | What it is for |
|---|---|
| `xhpcfep` (login nodes `fep01`, `fep02`) | log in, copy files, edit, submit jobs. **The SACLA data libraries refuse to load here** (`dbpy`, `stpy`, ...: "the import is prohibited"). |
| compute nodes (e.g. `thin03`) | everything that reads SACLA data. Reached through the PBS queue (`qsub`). |

The HPC is only reachable with the SACLA VPN. The laptop uses the ssh alias `sacla_HPC`, or
`--user <name>` in the scripts ([setup guide, step 1](setup-guide.md#1-ssh-to-the-hpc)).

## The right Python

The HPC has several Pythons. Only one has everything we need:

| Module | Python | numpy | pandas | matplotlib | h5py | mpi4py | SACLA libraries |
|---|---|---|---|---|---|---|---|
| **`python/SACLA_python-3.7/offline`** | **Intel 3.7.9** | 1.20.1 | 1.2.0 | 3.1.2 | 3.2.1 | 3.0.3 | **all** |
| `python/intelpython-3.7` | Intel 3.7.9 | same | same | same | same | same | all, but without the SACLA environment variables |
| `python/3.7.10_gcc-4.8.5` | 3.7.10 | 1.21.0 | – | 3.4.2 | 3.3.0 | – | all |
| `python/3.8.10_gcc-4.8.5` | 3.8.10 | 1.21.0 | – | 3.4.2 | 3.3.0 | – | none |
| `python/intelpython-3.9` | 3.9.19 | 1.26.4 | – | – | – | – | none |

So SILAS uses **`module load python/SACLA_python-3.7/offline`**. It lacks two things we
want, so the [one-time setup](#one-time-setup) installs them into `~/SILAS_py37` in your home:

- **IPython 7.34**, the last version for Python 3.7 (the only IPython on the HPC, 8.20 in
  `miniconda3`, needs Python 3.10). Without it the session uses the plain Python prompt.
- **tornado 6.2**, for showing plots in your browser (see [Plots on the HPC](#plots-on-the-hpc-in-the-browser)).

Nothing in the SACLA module changes; the session finds that folder by itself (it is tried for
every profile and skipped if it does not exist).

> **Not** `python/SACLA_python-3.7/online` for offline work: that module sets `OL_SIMPATH`, which
> switches `olpy` to the simulator.

### Where the SACLA libraries are

Each SACLA library has its own folder; `dbpy` and `stpy` are one level deeper than the
others:

```
/home/software/SACLA_tool/DataAccessUserAPI/latest/python/lib/dbpy/dbpy.cpython-37m-x86_64-linux-gnu.so
/home/software/SACLA_tool/DataAccessUserAPI/latest/python/lib/stpy/stpy.cpython-37m-x86_64-linux-gnu.so
/home/software/SACLA_tool/OnlineUserAPI/latest/python/lib/olpy.cpython-37m-x86_64-linux-gnu.so
/home/software/SACLA_tool/ImageProcessingUserAPI/latest/python/lib/ippy.cpython-37m-x86_64-linux-gnu.so
```

The module puts these folders (and those of SACLA's other libraries) on `PYTHONPATH` and sets
`SACLA_TOOL_HOME`, `BLSTAFF_CONF_DIR`, `LLPARDIR`, `DEFAULT_SIGNAL_BL1..3` and a few more. The `hpc`
[profile](profiles-and-modes.md) contains the same folders and variables, so the session also
works with only `python/intelpython-3.7` loaded; values set by the module always win.

## One-time setup

1. **Copy the code** from the laptop (repeat whenever the code changed; only changes are sent):

    ```bash
    cd /path/to/SILAS               # your copy of the code on the laptop
    bash scripts/sync_to_hpc.sh           # dry run: shows what would be copied
    bash scripts/sync_to_hpc.sh --go      # copy to ~/SILAS on the HPC
    ```

    or `sync_to_hpc(go=True)` in the laptop session. The HPC's own `config/` and `output/` are
    never copied over or deleted, so settings and results made on the HPC stay.

2. **Install the shortcuts, IPython and tornado** on the HPC (safe to repeat):

    ```bash
    ssh sacla_HPC
    bash ~/SILAS/scripts/install_hpc_env.sh        # dry run
    bash ~/SILAS/scripts/install_hpc_env.sh --go   # do it (backup: ~/.bashrc.before_silas)
    ```

    This adds one line to `~/.bashrc` (the shortcuts below) and installs IPython 7.34 and
    tornado 6.2 into `~/SILAS_py37` from `third_party/py37/`, which the copy in step 1
    brought along. Log in again; the login message then says `SILAS: bt_node to get a
    compute node ...`, and `bt` opens IPython (`In [1]:`).

    The packages are downloaded once on the laptop (the HPC has no internet):
    `bash scripts/fetch_hpc_packages.sh` puts them into `third_party/py37/` (built for Python 3.7
    on Linux x86_64). To install only them later: `bash ~/SILAS/scripts/install_hpc_packages.sh --go`.
    To remove them: delete `~/SILAS_py37`.

## Every time

```bash
ssh sacla_HPC          # plots open in the laptop's browser (below), no X windows needed
bt_node                # on the login node: interactive job on a compute node
bt                     # on the compute node: load the Python, cd ~/SILAS, start the session
```

### The shortcuts

| Command | Where | What it does |
|---|---|---|
| `bt_node [preset] [options]` | login node | interactive compute-node job (below) |
| `bt [options]` | compute node | `bt_env`, `cd ~/SILAS`, `python start_session.py --profile hpc` (options are passed on, e.g. `bt --plain`) |
| `bt_check` | compute node | `bt_env` + the environment check: packages, SACLA libraries, paths |
| `bt_jupyter [preset] [options]` | login node | a Jupyter server as a batch job; prints the tunnel and the link ([Jupyter notebooks](jupyter.md)); `-s` stops it, `--here` runs one on the compute node you are on |
| `bt_env` | compute node | only `module load python/SACLA_python-3.7/offline` and `SACLA_tool/offline` (SACLA's command-line tools, e.g. DataConvert4), e.g. before running a script |

They are defined in `scripts/hpc_env.sh`; `$SILAS_HOME` (default `~/SILAS`) and
`$SILAS_PROFILE` (default `hpc`) can be set before it is sourced.

### bt_node: choosing the resources

```
bt_node                    4 h,  6 cores,  24 GB   (default: the 6 workers of the offline tools)
bt_node small              1 h,  2 cores,   8 GB   quick checks
bt_node big                8 h, 16 cores,  64 GB   offline analysis, offline MPCCD view
bt_node mpi                4 h, 32 cores, 128 GB   MPI on one node
bt_node big -t 12          a preset with one value changed
bt_node -c 8 -m 32 -t 2    cores, memory (GB), hours
bt_node -q long -t 48      another queue
bt_node -x / -X            no X windows / force them
bt_node small -n           dry run: only print the qsub command
bt_node -h                 help with the queue limits
```

The offline tools (`analyse_scan`, the offline MPCCD viewer, `make_runlog`) use **6 worker
processes** by default (`workers=`, at most the cores of the job): more do not make reading
faster, the storage is the limit ([Scan analysis: speed](scans.md#speed)). So the default
`bt_node` has 6 cores; `bt_node big` (16 cores, 64 GB) is for the memory of the offline
MPCCD view of whole octal runs.

X windows (`-X`) are requested automatically when the login has a display (`ssh -Y`);
without one, `qsub -X` would fail with "DISPLAY not set". They are not needed: the plots of
SILAS open in the browser (below).

The offline MPCCD view keeps the loaded shots in RAM if they fit in half the job's memory
(about 2 GB per 1000 shots of 2 panels, 8 GB for 8 panels), otherwise on `/work`: for whole
runs of the octal MPCCD take `bt_node big`.

Queue limits (from the SACLA queue configuration); `bt_node` refuses requests above them:

| Queue | Max time | Cores | Memory | Note |
|---|---|---|---|---|
| `serial` (default) | 24 h | 1–48 | ≤ 160 GB | up to 20 jobs / 480 cores per user |
| `long` | 72 h | 1–12 | ≤ 160 GB | |
| `smp` | 24 h | 1–56 | ≤ 720 GB | large memory |
| `psmall`, `plarge` | 24 h | 2–960 | | several nodes: batch jobs only, not `bt_node` |

### Checking the environment

```bash
bt_check                                       # on a compute node
python -m silas.core.envcheck --profile hpc  # the same, without the shortcuts
```

```python
check_environment()                             # in the session
```

It shows the host (and warns on the login node), the Python, the versions of numpy, pandas,
matplotlib, h5py, scipy, mpi4py and IPython, where each SACLA library is loaded from, and the
SACLA environment variables.

## Plots on the HPC: in the browser

Windows from the compute nodes do not work: over X forwarding even a plain Tk canvas hangs
(a simple Tk text window does appear, matplotlib's do not). So on the `hpc`
profile all plots of SILAS (MPCCD viewer, scan viewer, `analyse_scan`) use
matplotlib's **WebAgg** backend: the compute node serves the figure as a web page and you open
it in the laptop's browser. When it starts, the terminal prints what to do, e.g.

```
==============================================================================
The plots are served by thin17 on port 8988.
On your LAPTOP (VPN on), in a new terminal, run (it prints nothing and stays open):

ssh -N -L 8988:127.0.0.1:8988 -J sacla_HPC <user>@thin17

then open in your browser:   http://localhost:8988/
...
==============================================================================
```

Copy the `ssh` line as printed: it has your HPC user name and the node the viewer runs on.
The compute nodes accept only ssh from the login node (any other port gives "No route to
host"), so the tunnel jumps through the login node (`-J sacla_HPC`) to an ssh login on the
compute node, which forwards to the viewer on the node itself (127.0.0.1). The viewer only
listens there, so other HPC machines cannot open the page. If you are asked for a one-time
password twice, that is the login node and then the node. If port 8988 is taken (another
user on the node), the next free one is used and printed.

Closing the browser tab does not stop the viewer: open the link again and it continues.
Stop it with `q` in the page or Ctrl-C in its terminal (it stops its worker processes and
removes a RAM cache, then the session prompt comes back), and do not leave it running when
you do not need it.

Stored runs: `start_mpccd_viewer(name=..., run=N)` shows the average of all shots of the run
(offline view, pump on / off with `w`); `replay=True` plays the run as if live, in a loop. See
the MPCCD viewer page.

The backend can be chosen per MPCCD array (`display.backend`), per profile (`"plot_backend"` in
`config/profiles.json`) or for one run (`SILAS_PLOT_BACKEND=TkAgg`).

Instead of the terminal session, the commands can also be used in a **Jupyter notebook** on a
compute node, with the plots and viewers in the notebook: see [Jupyter notebooks on the
HPC](jupyter.md).

## Long jobs without a terminal

A job that takes hours (e.g. the run log of a whole beamtime) should not depend on the
laptop's connection. The run logger has a batch job that runs on its own:

```bash
qsub -v LAST_RUN=1234999 ~/SILAS/scripts/runlog_job.sh    # on the login node
qstat -u $USER                                                  # is it running?
tail -f ~/SILAS/output/runlog_job.log                     # its output
```

See [Run logger](run-logger.md#as-a-batch-job-hpc). The same way: the time-tool log
(`scripts/tt_logger_job.sh`, [Time tool](timetool.md)) and the full-frame reduction
(`full_frame_job(...)` in the session or `scripts/fullframe_job.sh`, [Full-frame
reduction](fullframe.md)).

## Where to put data

| Folder | Size | Kept |
|---|---|---|
| `/home/<user>` | 100 GiB | not deleted automatically: **code** (`~/SILAS`) |
| `/work/<user>` | per group | **deleted 90 days after the last access**: temporary files |
| `/UserData/<user>` | 100 TiB | kept 3 years: **results** of the analysis |

`show_quota` shows the usage. On the HPC, point the output folder at `/UserData` once:
`cfg.set("paths.output", "/UserData/<user>/<experiment>")`.

SACLA keeps detector images (MPCCD) and SyncDB values readable from the HPC for **3 years**
after they were recorded; after that they go to tape (restore on request).

## The beamline analysis computers

During the beamtime the online analysis runs on `xu-bl3-anapc01/02` (DAQ-USER-LAN). The code is
copied there from the HPC; see [Working at the beamline](beamline.md).
