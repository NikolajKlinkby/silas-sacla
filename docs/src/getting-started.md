# Getting started

## What you need

- A copy of the `SILAS` folder on the computer you work on.
- **At SACLA** (beamline or HPC): nothing else; the SACLA Python and libraries are already
  installed and the matching [profile](profiles-and-modes.md) puts them on the Python path.
- **On a laptop**: the local SACLA environment `sacla_local_api/` (a Python 3.9 environment with
  the SACLA libraries and their type descriptions), used to check code before the beamtime
  ([Development notes](development.md#the-local-sacla-environment)).

## Starting the session

The session is started with `start_session.py` and the name of a profile.

### At the beamline (DAQ-USER-LAN)

On the analysis computers `xu-bl3-anapc01/02`, once set up (see [Working at the beamline](beamline.md)):

```bash
ssh -Y <user>@xu-bl3-anapc01
bl               # loads the SACLA Python and starts the session with the beamline profile
```

Without the setup, with the SACLA Intel Python 3.7 directly:

```bash
cd SILAS
/home/software/opt/intel/oneapi/intelpython/python3.7/bin/python3 start_session.py --profile beamline
```

### On the HPC (HPC-LAN)

Once set up (see [Working on the HPC](hpc.md)):

```bash
ssh sacla_HPC
bt_node          # interactive job on a compute node (bt_node -h: more cores, memory, time)
bt               # loads the SACLA Python and starts the session with the hpc profile
```

Plots (viewers, `analyse_scan`) open in the laptop's browser through an ssh
tunnel; the viewer prints the command to copy ([Plots on the HPC](hpc.md#plots-on-the-hpc-in-the-browser)).


### On a laptop

```bash
cd SILAS
source sacla_local_api/sacla_env.sh       # prompt shows "(sacla)"
python start_session.py                   # default profile: local
```

## What you see

The session prints a short banner, for example

```
SILAS session
  profile : beamline  (DAQ-USER-LAN at the beamline: online analysis, everything runs)
  mode    : live
  config  : .../SILAS/config/beamline.json
  profiles: .../SILAS/config/profiles.json
  beamline: 3   first_run: 1234400   output: .../SILAS/output/beamline
  python  : 3.7.9 (/home/software/opt/intel/oneapi/intelpython/python3.7/bin/python3)
  sys.path+ /home/software/opt/intel/oneapi/intelpython/python3.7/lib/python3.7/site-packages
  sys.path+ /home/software/SACLA_tool/DataAccessUserAPI/latest/python/lib
```

and then a prompt. If IPython is installed you get the IPython prompt (`In [1]:`),
otherwise the plain Python prompt (`>>>`) with tab completion. You can force the plain
prompt with `--plain`.

Lines starting with `MISSING` mean a path of the profile does not exist on this computer;
that is expected for the `beamline` profile on a laptop, but not at SACLA.

## The first time

Follow [Setting up a beamtime, step by step](setup-guide.md). Its first step in the session sets up
the experiment config of the profile (asks for the first run, the photon energy and the output
folder; see [Configuration](configuration.md#setting-it-up)):

```python
setup_config(beamline=3)
```

## First steps

```python
commands()                       # every command with a one-line description
help(start_online_logger)        # full description of one command
cfg.show()                       # the whole configuration of this profile
cfg.set("first_run", 1234400)    # first run of the beamtime (saved immediately)
newest_runs()                    # the newest runs of BL3 (needs SACLA)
start_online_logger()            # run log in its own terminal (needs SACLA)
scan_info()                      # the newest scan from the log: runs, motor, steps
scan_range(-2)                   # first and last run of the scan before
```

Leave the session with `exit()` or Ctrl-D. The configuration is saved as you go, so the
next session starts where you left off.

## Command line options

| Option | Meaning |
|---|---|
| `--profile NAME` | `local`, `beamline` or `hpc` (default: the `default` in `config/profiles.json`) |
| `--mode smoke` / `--mode live` | override the profile's mode for this session |
| `--config FILE` | use another configuration file instead of `config/<profile>.json` |
| `--plain` | plain Python prompt even if IPython exists |
