# Installing SILAS

SILAS is written on a laptop and runs at SACLA: on the **HPC** (offline analysis) and on the
**beamline analysis computers** (online analysis). The code travels **laptop → HPC → beamline**:
the laptop can reach the HPC through the VPN, the beamline computers can reach the HPC, but the
laptop cannot reach the beamline network.

| Step | Where | What |
|---|---|---|
| [1](#1-the-code) | laptop | get the code |
| [2](#2-ssh-to-the-hpc) | laptop | ssh to the HPC |
| [3](#3-the-local-sacla-environment-optional) | laptop | the local SACLA environment (optional, for development) |
| [4](#4-packages-for-sacla) | laptop | download the extra Python packages for SACLA's Python 3.7 |
| [5](#5-the-hpc) | laptop, HPC | copy the code to the HPC and install it there |
| [6](#6-the-beamline-analysis-computers) | beamline | copy the code from the HPC and install it there |
| [7](#7-the-configuration-and-the-documentation) | HPC / beamline, laptop | the experiment config; build the docs |

Afterwards, [Setting up a beamtime, step by step](docs/src/setup-guide.md) continues with the
configuration, the run log, the calibrations and the analysis.

## Requirements

- **SACLA accounts**: the HPC (`xhpcfep.hpc.spring8.or.jp`) and, for the beamtime, the beamline
  analysis computers (`xu-bl3-anapc01/02`, reached from the opcon computers).
- **The SPring-8 / SACLA VPN** on the laptop, to reach the HPC.
- **Laptop**: Linux with bash, ssh and rsync; Python 3 with pip (to download packages and to build
  the docs); for the local SACLA environment Python 3.9 (e.g. with `uv`).
- **At SACLA** nothing has to be installed system-wide: the code uses SACLA's Intel Python 3.7
  and its libraries; the extra packages go into folders in your home.

## 1. The code

```bash
git clone https://github.com/NikolajKlinkby/silas-sacla.git SILAS
cd SILAS
```

All commands below are run from this folder unless they say otherwise. (The folder name is free on
the laptop; on the HPC and at the beamline the code always goes to `~/SILAS`.)

## 2. ssh to the HPC

The scripts reach the HPC under the ssh name `sacla_HPC`. Add to `~/.ssh/config`:

```
Host sacla_HPC
    HostName xhpcfep.hpc.spring8.or.jp
    User <your HPC user name>
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 60
```

and set up a key login (VPN on):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519      # skip if the key exists
ssh-copy-id -i ~/.ssh/id_ed25519.pub sacla_HPC  # asks for the HPC password once
ssh sacla_HPC hostname                          # logs in without a password
```

Without the ssh config, every laptop script takes `--user <name>` instead
(`bash scripts/sync_to_hpc.sh --user <name> --go`, likewise `check_and_sync.sh`,
`hpc_jupyter.sh`, `fetch_sacla.sh`). Details: [setup guide, step 1](docs/src/setup-guide.md#1-ssh-to-the-hpc).

## 3. The local SACLA environment (optional)

For development on the laptop: SACLA's Python libraries in a Python 3.9 environment, with type
stubs of every SACLA function (editor completion, `sacla_check`) and SACLA's MPCCD simulator for
`olpy`. Not needed to use SILAS at SACLA. SACLA's files are copied from the HPC for your own
use; they are not part of this repository.

```bash
cd sacla_local_api
uv python install 3.9                 # a private Python 3.9 (or any python3.9; uv: https://docs.astral.sh/uv/)
bash scripts/fetch_sacla.sh           # dry run: what would be copied from the HPC (VPN on)
bash scripts/fetch_sacla.sh --go      # copy SACLA's libraries and the simulator data into software/ (~180 MB)
bash scripts/setup_env.sh             # venv/, the SACLA wheels, the stubs, .env, a smoke test
cd ..
```

Use it:

```bash
source sacla_local_api/sacla_env.sh   # prompt shows (sacla); sacla_off leaves
python start_session.py               # the session with the local profile (SACLA calls are only checked)
bash scripts/check_and_sync.sh --no-sync   # type check (sacla_check) and the tests
```

**VS Code**: `mkdir -p .vscode && cp sacla_local_api/vscode_settings.json .vscode/settings.json`,
open the folder and select the interpreter `sacla_local_api/venv/bin/python`.

After moving the folder, run `bash sacla_local_api/scripts/setup_env.sh --fresh` (the environment
contains absolute paths). Details: [Development notes](docs/src/development.md#the-local-sacla-environment),
`sacla_local_api/README.md`.

## 4. Packages for SACLA

SACLA's Python 3.7 lacks IPython (the session prompt), tornado (plots in the browser on the HPC)
and Jupyter. The HPC and the beamline computers have no internet, so the packages are downloaded
on the laptop as wheels for Python 3.7 and copied with the code:

```bash
bash scripts/fetch_hpc_packages.sh        # IPython 7.34, tornado 6.2 -> third_party/py37/
bash scripts/fetch_jupyter_packages.sh    # optional: JupyterLab 3.6, ipympl 0.7 -> third_party/jupyter_py37/
```

## 5. The HPC

Copy the code (and the packages) to `~/SILAS` on the HPC; repeat whenever the code changed
(only changes are sent; the HPC's `config/` and `output/` are never touched):

```bash
bash scripts/sync_to_hpc.sh               # dry run
bash scripts/sync_to_hpc.sh --go          # copy
```

Install on the HPC login node (safe to repeat; dry run without `--go`):

```bash
ssh sacla_HPC
bash ~/SILAS/scripts/install_hpc_env.sh --go
exit
```

It adds the SILAS shortcuts to `~/.bashrc` (a backup `~/.bashrc.before_silas` is kept),
installs IPython and tornado into `~/SILAS_py37` and, if downloaded, Jupyter into
`~/SILAS_jupyter`. Check it:

```bash
ssh sacla_HPC
bt_node            # an interactive job on a compute node
bt_check           # the packages and the SACLA libraries are found
bt                 # the session (IPython)
```

Jupyter: `bash scripts/hpc_jupyter.sh --start` on the laptop starts a server as a batch job and
opens it in the browser. Details: [Working on the HPC](docs/src/hpc.md),
[Jupyter notebooks](docs/src/jupyter.md).

## 6. The beamline analysis computers

During the beamtime, logged in to `xu-bl3-anapc01` (or `02`) with `ssh -Y` (the viewers open
windows). The code comes from your folder on the HPC:

```bash
scp <user>@xhpcfep.hpc.spring8.or.jp:SILAS/scripts/fetch_from_hpc.sh .   # the first time only
bash fetch_from_hpc.sh                    # dry run
bash fetch_from_hpc.sh --go               # copy ~/SILAS from the HPC
bash fetch_from_hpc.sh --go --config      # ... and the HPC's experiment config as config/beamline.json
bash ~/SILAS/scripts/install_beamline_env.sh        # report of the computer + dry run
bash ~/SILAS/scripts/install_beamline_env.sh --go   # shortcuts, IPython and tornado
```

Log in again; `bl` starts the session with the beamline profile, `bl_check` checks the environment,
`bl_update --go` fetches newer code from the HPC. The installer was written before we had access
to these computers: its report (`output/beamline_probe_<host>.txt`) says what to adapt.
Details: [Working at the beamline](docs/src/beamline.md).

## 7. The configuration and the documentation

**Configuration**, once per place (HPC, beamline), in the session:

```python
setup_config(beamline=3)      # asks for the first run, the photon energy and the output folder
```

(or `bash scripts/setup_config.sh --beamline 3`). Then follow the
[setup guide](docs/src/setup-guide.md) from step 3.

**Documentation** (laptop):

```bash
pip install markdown markdown-it-py
python docs/sacla_manuals/build_all.py --download   # optional, VPN on: a local copy of SACLA's manuals
python docs/build_docs.py                           # -> docs/html/index.html (and the SACLA manuals)
```

## Updating

On the laptop: `git pull`, then `bash scripts/check_and_sync.sh` (checks, then copies to the HPC).
At the beamline: `bl_update --go`. Re-run the installers only when they changed or a package was
added.

## Uninstalling

- HPC: delete `~/SILAS`, `~/SILAS_py37`, `~/SILAS_jupyter`, and the line marked
  `# SILAS shortcuts` in `~/.bashrc`.
- Beamline: delete `~/SILAS`, `~/SILAS_py37`, and the line marked
  `# SILAS beamline shortcuts` in `~/.bashrc`.
- Results stay in the output folders you chose (`/UserData/<user>/...`).
