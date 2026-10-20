# Development notes

Rules and tools for working on SILAS.

## Rules for the code

- **Python 3.7.** SACLA's Python is 3.7, while the laptop environment is 3.9. Do not use
  newer syntax: no `:=`, no `f"{x=}"`, no `/` in function signatures, no `list[int]` /
  `dict[str, int]` in annotations (use `typing.List`, `typing.Dict`), no `dict1 | dict2`,
  no `str.removeprefix`.
- **`core` is shared.** Anything that works the same online and offline goes into
  `silas/core`. Differences between online and offline are a keyword argument there, or a
  thin script in `online/` / `offline/`.
- **SACLA libraries are imported inside functions**, not at the top of a module
  (`def f(): import dbpy ...`). The package then imports anywhere (also on the HPC login
  node, where the SACLA libraries refuse to load), and `sacla_check` still checks every
  call.
- **SACLA calls go through `silas.core.db`** (and later similar modules for images). The
  SACLA rules (positional arguments, tuples, the high tag of the run) are handled there once.
- **Entry points are marked `@sacla.requires_sacla`**: session commands and the `main()` of
  scripts that need SACLA.
- **The bootstrap uses the standard library only.** `silas/core/bootstrap.py` runs before
  the profile's paths are set, so it must not import numpy, pandas or SACLA modules.
- **Process pools** (`multiprocessing`, context "spawn") get
  `initializer=runner.worker_ignores_ctrl_c`: Ctrl-C reaches every process of the terminal,
  the workers ignore it and the main process stops them, so a stop is clean.
- **Write good docstrings.** The reference part of this documentation is generated from
  them; the first line of a session command's docstring is what `commands()` prints.

## The local SACLA environment

`sacla_local_api/` makes the SACLA Python libraries usable on a Linux laptop, so code can be written,
type-checked and partly run before it goes to SACLA:

- a Python 3.9 environment (`venv/`) with SACLA's own Python 3.9 builds of `olpy`, `dbpy`, `stpy`,
  `ippy`, `tmapy` and the CITIUS modules, copied from the HPC (`software/`);
- **stubs** (`stubs/src/*.pyi`): hand-written descriptions of every SACLA function (arguments,
  return values, docstrings), so the editor completes and documents them and `sacla_check`
  (mypy) finds wrong calls without SACLA;
- `olpy` runs against SACLA's **MPCCD simulator** (`OL_SIMPATH`), `ippy` runs as it is; `dbpy` and
  `stpy` import but need SACLA's servers (the tests use fakes).

**Setting it up** (once; VPN on, the ssh alias from [step 1 of the setup guide](setup-guide.md#1-ssh-to-the-hpc),
or `--user <name>`):

```bash
cd SILAS/sacla_local_api
uv python install 3.9                 # a private Python 3.9 (uv: https://docs.astral.sh/uv/)
bash scripts/fetch_sacla.sh           # dry run: what would be copied from the HPC
bash scripts/fetch_sacla.sh --go      # copy the libraries and simulator data into software/ (~180 MB)
bash scripts/setup_env.sh             # venv, SACLA wheels, stubs, .env, a smoke test
```

On a new clone the fetch is needed once (SACLA's files are not in the repository); afterwards
only to update them. `setup_env.sh --fresh` builds the environment again; do that also after moving the folder (the venv and `.env` contain 
absolute paths). `sacla_local_api/README.md` has the details (layout, missing system libraries).

**Using it:**

```bash
source sacla_local_api/sacla_env.sh   # in SILAS; the prompt shows (sacla); sacla_off leaves
sacla_check silas/core/db.py       # type check against the SACLA stubs
python start_session.py               # the session, profile local (smoke mode)
```

**VS Code.** Copy the settings template into the workspace:

```bash
mkdir -p .vscode && cp sacla_local_api/vscode_settings.json .vscode/settings.json
```

```json
{
    "python.defaultInterpreterPath": "${workspaceFolder}/sacla_local_api/venv/bin/python",
    "python.envFile": "${workspaceFolder}/sacla_local_api/.env",
    "terminal.integrated.profiles.linux": {
        "SACLA_Env": {"path": "/bin/bash",
                      "args": ["--init-file", "${workspaceFolder}/sacla_local_api/vscode_init.sh"]}
    },
    "terminal.integrated.defaultProfile.linux": "SACLA_Env"
}
```

Open the SILAS folder in VS Code and choose the interpreter `sacla_local_api/venv/bin/python`
(Python: Select Interpreter) if it does not pick it by itself. Then:

- completion, hover documentation and type errors for every SACLA function come from the stubs;
- `python.envFile` gives runs and debugging the same variables as `sacla_env.sh` (simulator,
  library paths);
- new terminals start with the SACLA environment active (`vscode_init.sh` loads your `~/.bashrc`,
  then `sacla_env.sh`), so `sacla_check` and `bash scripts/check_and_sync.sh` work there.

If a terminal does not start with `(sacla)`, the editor did not replace `${workspaceFolder}` in the
terminal profile: write the full path to `vscode_init.sh` there instead.

**Changing a stub:** edit `sacla_local_api/stubs/src/<module>.pyi`, then `bash
sacla_local_api/scripts/setup_env.sh` (it rebuilds the stubs) and `sacla_check --selftest`.

## The SACLA manuals

`docs/sacla_manuals/` is a local copy of SACLA's software documentation (the HPC portal, VPN
only): every manual as PDF, the overview pages, the module list and the examples, as a website
(`docs/sacla_manuals/html/`) and as markdown. `python docs/build_docs.py` rebuilds it from the
files on disk and links it in the sidebar (Reference → SACLA manuals). New or changed manuals are
downloaded on the VPN:

```bash
pip install -r docs/sacla_manuals/requirements.txt    # once (markdown-it-py)
python docs/sacla_manuals/build_all.py --download     # fetch new or missing files, then build
python docs/sacla_manuals/build_all.py --refresh      # also files that changed on the portal
```

The documentation belongs to RIKEN / JASRI: only the builder is in the repository; the downloaded
files and the generated pages stay local (`.gitignore`).

## Checking code

All of it in one go, and then copy the code to the HPC if everything passed (laptop, VPN for
the copy):

```bash
bash scripts/check_and_sync.sh               # checks + sync_to_hpc --go
bash scripts/check_and_sync.sh --no-sync     # checks only
bash scripts/check_and_sync.sh --user <name> # sync without the ssh alias
```

Or step by step (after `source sacla_local_api/sacla_env.sh`):

```bash
sacla_check silas start_session.py scripts tests   # type check against the SACLA signatures
python tests/test_runlog_fake.py                     # run logger logic with a fake dbpy
python tests/test_scans_fake.py                      # scan table: grouping, motor search, session commands
python tests/test_mpccd_pipeline.py                  # MPCCD reading, dark, keV threshold, calibration, assembly
python tests/test_roi.py                             # ROIs: config, panel pixels, sums, speed
python tests/test_scan_view.py                       # scan viewer: keeping up, lost shots, sums, ratio, logger status
python tests/test_scan_analysis.py                   # offline scan analysis: shot table, quantiles, MPI split (fake stpy)
python tests/check_geometry_ippy.py                  # our assembly vs SACLA's ippy.reconstruction
```

Probes that read real data (HPC compute node, read-only):

```bash
python tests/probe_scan_info.py 1234501 1234502      # everything dbpy knows about runs of a scan
python tests/adu_histogram.py                        # ADU per photon of the MPCCD panels (energy calibration)
python tests/probe_scan_speed.py --scan 1234501 --name octal   # where the scan analysis spends its time
python tests/bench_workers.py --scan 1234501 --name octal      # shots/s against the number of workers (graph)
```

`sacla_check` runs mypy with descriptions ("stubs") of every SACLA function, so wrong
function names, arguments and return value use are found without SACLA access. Tests in
`tests/` replace the SACLA library with a small fake so the logic can run anywhere.

## Building this documentation

```bash
pip install markdown markdown-it-py      # once
python docs/build_docs.py                # -> docs/html/index.html, and the SACLA manuals
python docs/build_docs.py --no-manuals   # without rebuilding the SACLA manuals
```

- Guide pages are Markdown files in `docs/src/`. Their sidebar sections, order and titles are the
  `CATEGORIES` list at the top of `docs/build_docs.py`; a new page must be added there.
  Link to other pages with their `.md` name, e.g. `[Run logger](run-logger.md)`.
- The reference pages (one per module) and the [Session commands](commands.html) page are
  generated from the code. The code is read, not imported, so building never needs SACLA.
- `docs/html/` is generated: do not edit it by hand. It is self-contained and can be copied
  anywhere and opened in a browser without internet. Press `/` to jump to the search box.
