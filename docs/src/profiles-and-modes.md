# Profiles and modes

## Profiles: where am I working?

A **profile** describes one place where the software runs. There are three:

| Profile | Where | Mode | Extra Python paths |
|---|---|---|---|
| `local` | your laptop | `smoke` | none (`sacla_local_api` provides the libraries) |
| `beamline` | DAQ-USER-LAN | `live` | SACLA site-packages, SACLA API |
| `hpc` | HPC compute node | `live` | SACLA site-packages, SACLA API |

The profiles are stored in `config/profiles.json` (created on first start). Each profile has

- `sys_paths`: folders put at the front of Python's search path (`sys.path`) **before
  anything else is imported**. At SACLA these are where the SACLA libraries and the Python
  packages (numpy, pandas, ...) of the SACLA Python live. The first entry has the highest
  priority.
- `mode`: `smoke` or `live` (below).
- `terminal`: the command used to open a new terminal window for scripts that run on their
  own (e.g. `["xterm", "-e"]`); `null` where there is no display. The window stays open
  after the script ends (press Enter to close it), so error messages can be read.
- `mpirun`: the MPI start command (default `mpirun`), used for scripts that run on several
  processes such as the [MPCCD viewer](mpccd-viewer.md).
- `description`: shown in the banner.

You can edit `profiles.json` with a text editor. Changes take effect at the next session,
because the Python path is only set at start-up.

### Choosing the profile

1. `--profile NAME` when starting the session, or the environment variable `SILAS_PROFILE`;
2. otherwise `"default"` in `profiles.json` (initially `local`).

`cfg.use_profile("hpc")` changes the default for the next session.

### Why the paths must come first

The SACLA computers have several Pythons and several copies of numpy and friends. The
profile's paths make sure the session, and every script it starts, uses the right ones. This
happens automatically when the `silas` package is imported, so **every script must start
with `import silas`** before importing numpy, pandas or a SACLA library.

## Modes: smoke or live

Some functions need a SACLA library and SACLA's servers, which only exist at SACLA. The
**mode** decides what happens when such a function is called:

| Mode | What happens |
|---|---|
| `live` | the function runs normally. Default at the beamline and on the HPC. |
| `smoke` | the function is **not run**. Instead its source file is checked with `sacla_check`, which compares every SACLA call with the official function signatures (wrong names, wrong number or type of arguments, a list where a tuple is needed, ...). Default on a laptop. |

So on a laptop you can call `start_online_logger()` or `newest_runs()` and get
`OK: no problems found` instead of a crash, and the same command really runs at SACLA.

Change the mode for the current session with `set_mode("live")` / `set_mode("smoke")` or
start with `--mode`. Scripts started from the session inherit it.

> **Note** `sacla_check` comes from the local SACLA environment: source `sacla_local_api/sacla_env.sh`
> before starting the session, otherwise smoke checks report that they cannot run.

### Marking a function as needing SACLA

Functions are marked with the `@requires_sacla` decorator:

```python
from silas.core import sacla

@sacla.requires_sacla
def newest_run():
    import dbpy
    return dbpy.read_runnumber_newest(3)
```

In smoke mode the call prints `[smoke] newest_run() not executed` and smoke-checks the file;
in live mode it runs. Only *entry points* (session commands, the `main()` of scripts) are
marked; the helper functions they call are not.
