# Working at the beamline

During the beamtime the online analysis (run logger, MPCCD viewer, time-tool viewer and logger,
scan viewer) runs on the beamline's analysis computers, `xu-bl3-anapc01` and `xu-bl3-anapc02`.
They are on DAQ-USER-LAN, reached by ssh from the opcon computers, and only during beam time. They
have their own home folder (100 GiB), separate from the HPC.

> **Not yet tested on site.** The scripts on this page were written before we had access to the
> analysis computers. What they assume (SACLA's Python 3.7 there, the module
> `python/SACLA_python-3.7/online`, ssh from there to the HPC) is checked by the first step of
> the setup; its report says what to change.

```bash
ssh -Y <user>@xu-bl3-anapc01                         # -Y: the viewers open windows
bash ~/SILAS/scripts/install_beamline_env.sh --go   # once
bl                                                  # start the session (profile beamline)
```

## Getting the code there

The laptop is not on DAQ-USER-LAN, so the code takes the route **laptop → HPC → beamline**:
`sync_to_hpc.sh` on the laptop as always, then on the analysis computer:

```bash
scp <user>@xhpcfep.hpc.spring8.or.jp:SILAS/scripts/fetch_from_hpc.sh .   # the first time only
bash fetch_from_hpc.sh             # dry run: lists what would be copied
bash fetch_from_hpc.sh --go        # copy ~/SILAS from the HPC to ~/SILAS here
bash fetch_from_hpc.sh --go --config   # also the HPC's experiment config as config/beamline.json
```

Later `bl_update --go` does the same. As `sync_to_hpc.sh`, it never copies `config/` (except
with `--config`, and then only if there is no `config/beamline.json` yet; `--force` replaces it,
with a backup) or `output/`. It uses rsync, or tar through ssh when one side has no rsync.

`--config` brings the settings prepared on the HPC (detectors and aliases, ROIs, threshold, time
tool, scan settings) to the beamline. Check `paths.output` afterwards: an HPC folder such as
`/UserData/...` may not exist here (`cfg.set("paths.output", ...)`, or delete the key for the
default `output/beamline/`).

## One-time setup

```bash
bash ~/SILAS/scripts/install_beamline_env.sh        # dry run
bash ~/SILAS/scripts/install_beamline_env.sh --go   # do it (safe to repeat)
```

| Step | What |
|---|---|
| 0 | a report of the computer: `output/beamline_probe_<host>.txt` (`scripts/beamline_probe.sh`) |
| 1 | the shortcuts (`scripts/beamline_env.sh`) in `~/.bashrc`, active on the `xu-*` computers only |
| 2 | IPython and tornado into `~/SILAS_py37` from `third_party/py37/` (as on the HPC), unless the SACLA Python has them |
| 3 | the environment check with the beamline profile (`envcheck`) |

It changes nothing in the SACLA installation. Undo: remove the line marked
`# SILAS beamline shortcuts` from `~/.bashrc` (a backup `~/.bashrc.before_silas` is made)
and delete `~/SILAS_py37`.

The wheels for step 2 come from the laptop (`bash scripts/fetch_hpc_packages.sh`, then sync to the
HPC and `bl_update --go`); the analysis computers probably have no internet either.

## The shortcuts

| Command | What |
|---|---|
| `bl [options]` | `bl_env`, then the session with the beamline profile (options as `start_session.py`) |
| `bl_env` | the SACLA Python 3.7: `module load python/SACLA_python-3.7/online`, or SACLA's Intel Python by its path when there are no modules |
| `bl_check` | the environment check (packages, SACLA libraries, paths) |
| `bl_probe [--online]` | the report of step 0 again; `--online` also asks olpy for the online detector list |
| `bl_update [--go]` | the newest code from the HPC (`fetch_from_hpc.sh`) |

`bl_env` warns when `OL_SIMPATH` is set: then olpy reads SACLA's simulator, not the detectors (the
same module on the HPC is the online *simulation*).

## What is different from the HPC

- No batch jobs: everything runs on the analysis computer you are logged in to. Commands that
  open a script in a new terminal (the loggers, the online viewers) use `xterm` (profile setting
  `terminal`), so log in with `ssh -Y`.
- Plots and viewers are windows (Tk), not the browser.
- The online tools read the detectors now (olpy) and the run database (dbpy); the offline tools
  (stpy, `analyse_scan`) also work here when the runs are stored, but long work belongs on the HPC.
- Output goes to `output/beamline/` in SILAS (`paths.output`), in the home folder of the
  analysis computer: copy what you want to keep to the HPC before the beamtime ends.

## If something is missing

Send or keep the report (`output/beamline_probe_<host>.txt`). The usual cases:

| Report says | Then |
|---|---|
| `module command: NO` and the Intel Python found | fine: `bl_env` uses the Python by its path, the beamline profile adds the SACLA libraries |
| no Python 3.7 at all | ask the beamline staff which Python has the SACLA libraries (`dbpy`, `olpy`) here, and set `SILAS_PY_MODULE` (a module name) in `~/.bashrc` before the SILAS line |
| SACLA libraries `--` in the envcheck part | the paths in `silas/core/bootstrap.py` (`_SACLA_API_PATH`, `_HPC_PATHS`) differ here: add the right folder to the beamline profile's `sys_paths` in `config/profiles.json` |
| `OL_SIMPATH` set | the simulator is active: `unset OL_SIMPATH`, and find out why it is set |
| `xterm NOT FOUND` | set the profile's `terminal` to one that exists (e.g. `["gnome-terminal", "--"]`) |
| no key login to the HPC | `fetch_from_hpc.sh` asks for the password; or `ssh-keygen` and `ssh-copy-id <user>@xhpcfep.hpc.spring8.or.jp` |
