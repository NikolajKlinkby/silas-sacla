# SACLA Python APIs – local setup

Run the SACLA Python APIs on the laptop (Python 3.9 venv), in particular the **online-simulation
mode** of the OnlineUserAPI (`olpy`), to develop online/offline analysis code without the HPC.
Part of SILAS (`SILAS/sacla_local_api/`); the setup is also described in the SILAS
docs, *Development notes → The local SACLA environment*. Reference docs: `../docs/sacla_manuals`
(see `../docs/sacla_manuals/html/index.html`).

## Daily use

```bash
source sacla_local_api/sacla_env.sh     # in SILAS: on  (prompt shows "(sacla)")
sacla_off                                                          # off
```
VS Code: interpreter `sacla_local_api/venv/bin/python`, `"python.envFile": "${workspaceFolder}/sacla_local_api/.env"`
(the whole settings file: `sacla_local_api/vscode_settings.json`).

```bash
sacla_check my_script.py        # static smoke test of a script against the API stubs (mypy)
sacla_check --stubtest          # stubs vs. compiled modules
sacla_check --selftest          # proves the stubs are active (6 planted errors)
python examples/olpy_sim_smoke.py   # real run against the MPCCD simulator
```

## Rebuild from scratch

### 1. Access to the SACLA HPC (one-time)

The HPC front end `xhpcfep` (hpc.spring8.or.jp) is only reachable **with the SACLA VPN connected**.
All scripts use the ssh alias `sacla_HPC`, defined in `~/.ssh/config` (or give `--user <name>`
to `fetch_sacla.sh`, which then connects to `<name>@xhpcfep.hpc.spring8.or.jp` without the alias):

```
Host sacla_HPC
    HostName xhpcfep.hpc.spring8.or.jp
    User <your HPC user name>
    IdentityFile ~/.ssh/id_rsa
```

Key-based login (so rsync/ssh do not ask for a password every time):

```bash
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa      # skip if ~/.ssh/id_rsa already exists; a passphrase is recommended
ssh-copy-id -i ~/.ssh/id_rsa.pub sacla_HPC      # VPN on; asks for the HPC password once
ssh sacla_HPC hostname                          # should log in without a password and print the node name
```

`ssh-copy-id` appends the public key to `~/.ssh/authorized_keys` on the HPC. If you used a passphrase,
`ssh-add ~/.ssh/id_rsa` once per login session avoids retyping it.

### 2. Python, download and environment

```bash
uv python install 3.9                       # private Python 3.9, system python untouched
                                            # (uv: curl -LsSf https://astral.sh/uv/install.sh | sh)
ssh sacla_HPC 'bash -l -s' < scripts/hpc_probe.sh > hpc/probe_report.txt 2>&1   # optional survey (VPN on)
bash scripts/fetch_sacla.sh                 # dry run (VPN on) -> hpc/dryrun_report.txt
bash scripts/fetch_sacla.sh --go            # download into software/ (~180 MB)
bash scripts/setup_env.sh [--fresh]         # venv, wheels, stubs, .env, smoke test
                                            # (--fresh also after moving this folder)
```

## Layout

```
sacla_local_api/
├── sacla_env.sh            source to activate (env vars of HPC module SACLA_env_online + LD_LIBRARY_PATH)
├── .env                    same variables for VS Code (written by setup_env.sh)
├── venv/                   Python 3.9 venv: numpy<2, h5py, matplotlib, ipython, ipykernel, mypy,
│                           7 SACLA wheels + generated <module>-stubs
├── stubs/                  hand-written API stubs: src/<module>.pyi, constants/, build_stubs.py
├── examples/olpy_sim_smoke.py   reads 3 tags from every simulated MPCCD detector
├── scripts/
│   ├── sacla_check.sh      mypy smoke test / stubtest / selftest (shell function sacla_check)
│   ├── hpc_probe.sh        read-only HPC survey -> hpc/probe_report.txt
│   ├── fetch_sacla.sh      minimal rsync from HPC (dry run by default, --go to download)
│   └── setup_env.sh        builds the venv
├── hpc/                    software_listing.txt, probe_report.txt, dryrun_report.txt
└── software/               mirror of the needed HPC files (~180 MB)
    ├── SACLA_tool/<API>/<ver>/   python/3.9 wheels, binding .cpp, include/, examples; latest -> symlinks
    ├── SACLA_tool/.config/       config files + ol_simdata (HPC copy, untouched)
    ├── SACLA_tool/local/zeromq…  shared libs (ctolpy_xfel)
    ├── local/boost…, local/hdf5… shared libs (dbpy, stpy, ctdapy/ctolpy)
    ├── compat/lib/               CentOS 7 system libs missing on Ubuntu (mysqlclient 18, openssl 1.0,
    │                             openldap 2.4, sasl2.so.3, ICU 50); only sonames ldconfig lacks
    └── ol_simdata_mpccd/         OL_SIMPATH: symlinks to ol_simdata + MPCCD-only DetectorsConf.lst
```

