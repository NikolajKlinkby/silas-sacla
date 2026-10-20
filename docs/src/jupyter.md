# Jupyter notebooks on the HPC

Everything the session can do also works in a **Jupyter notebook** that runs on an HPC
compute node and is opened in the browser of your laptop. The notebook has the same commands
as the terminal session (`bt`), and the plots appear in the notebook as interactive matplotlib
figures (zoom, pan, save): the analysis plots below the cell that made them, and the viewers
(the MPCCD viewer, the time-tool viewer, the full-frame viewer, ...) with their keys and the mouse.

```bash
bash scripts/hpc_jupyter.sh --start      # laptop, in your SILAS folder: start a server, tunnel, link
```

Every notebook starts with the session loaded: `show_scans()`, `analyse_scan(...)`, `commands()`
work in the first cell, as in `bt`.

## One-time setup

The SACLA Python on the HPC has no Jupyter. JupyterLab and the notebook widgets are therefore
installed into a folder of their own, `~/SILAS_jupyter`, that **only the Jupyter server** puts
on the path. Everything else comes from the same places as in the terminal session (`bt`): numpy,
pandas, matplotlib 3.1, h5py, ... from the SACLA Python, IPython and tornado from
`~/SILAS_py37`. As for IPython ([Working on the HPC](hpc.md#one-time-setup)), the HPC has no
internet, so the packages are downloaded on the laptop and copied over:

```bash
bash scripts/fetch_jupyter_packages.sh             # laptop: wheels into third_party/jupyter_py37/
bash scripts/sync_to_hpc.sh --go                   # copy them with the code
bash ~/SILAS/scripts/install_hpc_jupyter.sh --go   # HPC (login node): install and test them
```

| Package | Version | For |
|---|---|---|
| JupyterLab | 3.6 | the notebook server (the last series for Python 3.7) |
| ipykernel | 6.16 | the notebooks' Python |
| ipywidgets | 7.7 | widgets in the notebook |
| ipympl | 0.7.0 | interactive matplotlib figures in the notebook (the viewers); the last version that works with the SACLA Python's matplotlib 3.1 |

`fetch_jupyter_packages.sh` downloads them with their dependencies, leaves out what the SACLA Python
has (matplotlib, numpy, Pillow, ...) and writes the list of wheels to `wheels.txt`;
`install_hpc_jupyter.sh` empties `~/SILAS_jupyter` and installs exactly that list.

The install first checks, with the HPC's Python 3.7, that every dependency of the installed
packages is there (`scripts/check_target_deps.py`). pip on the laptop decides dependencies with the
laptop's Python, so packages that only Python 3.7 needs can be missing; `fetch_jupyter_packages.sh`
names the known ones, and if the check still finds some it prints the line to run on the laptop
(`bash scripts/fetch_jupyter_packages.sh "name>=version" ...`, then sync and install again). Then it
imports the packages and checks JupyterLab's pages and the widget extensions.

`install_hpc_env.sh --go` also does the last step when the wheels are there. Repeating the install
is safe. Undo: delete `~/SILAS_jupyter`.

## Starting a server

The server runs as a **batch job** on a compute node (`scripts/jupyter_job.sh`), so it does not
depend on your laptop's connection: close the laptop, come back later and connect again.

From the laptop (VPN on), in your SILAS folder:

```bash
bash scripts/hpc_jupyter.sh --start             # submit the job (8 h, 6 cores, 24 GB), wait, then connect
bash scripts/hpc_jupyter.sh --start big -t 12   # 16 cores, 64 GB, 12 h (options as bt_node)
bash scripts/hpc_jupyter.sh                     # connect again to the running server
bash scripts/hpc_jupyter.sh --stop              # stop it (qdel)
bash scripts/hpc_jupyter.sh --user <name> ...   # without the sacla_HPC alias in ~/.ssh/config
```

`hpc_jupyter.sh` opens the ssh tunnel in its terminal (it prints nothing more and stays open; Ctrl-C
closes the tunnel, not the server) and opens the link in your browser. Or the same by hand, on the
HPC login node:

```bash
bt_jupyter                      # submit, wait, print the tunnel command and the link
bt_jupyter big -t 12            # presets and options as bt_node
bt_jupyter -s                   # stop the running server
bt_jupyter --here               # on a compute node you are on (bt_node): the server in the foreground
```

and on the laptop the printed tunnel, e.g.

```
ssh -N -L 8890:127.0.0.1:8890 -J sacla_HPC <user>@thin17
```

then the printed link `http://localhost:8890/lab?token=...`. The tunnel works like the one for
the [plots in the browser](hpc.md#plots-on-the-hpc-in-the-browser): through the login node to an
ssh login on the compute node; the server only listens on the node itself, and the token in the
link is its password. Node, port and token are written to
`~/SILAS/output/jupyter/current.json` (readable only by you); the server's output goes to
`~/SILAS/output/jupyter/jupyter_job.log`.

**Resources.** The notebook's kernel does the work of the commands (reading runs, the analysis), so
ask for what the work needs: the default (6 cores, 24 GB) suits the offline tools with their 6
workers; whole octal runs in the offline MPCCD viewer need `big`. The server stops at the end of
the walltime (default 8 h), and with it every notebook's kernel.

## In a notebook

The JupyterLab file browser starts in your home folder. `~/SILAS/notebooks/start_here.ipynb`
is a starting point; it is part of the code and replaced when the code is synced, so **save your
own notebooks elsewhere**, e.g. `/UserData/<user>/notebooks/`.

The notebooks' kernel, **SILAS (Python 3.7)** (it replaces the default "Python 3" kernel),
loads the session when it starts: profile `hpc`, `config/hpc.json`, every command and `cfg`.
Restarting the kernel loads it again. Only for another experiment config:

```python
from silas.cli.notebook import start
start(config="config/other.json")        # the session again, with another experiment config
commands()                               # what is there
```

Everything works as in the terminal session:

| What | In the notebook |
|---|---|
| commands with text output (`scan_info`, `show_scans`, `make_runlog`, ...) | the output below the cell |
| plots (`analyse_scan`, `calibrate_tt`, `compare_tt_sacla`, ...) | interactive figures below the cell (zoom, pan, save; `plot_tt`'s keys); the files are saved as always |
| viewers (`start_mpccd_viewer(run=...)`, `start_tt_viewer(run=...)`, `view_full_frame`, ...) | an interactive figure below the cell: click on it, then its keys work (`h` help, `q` stops); the cell finishes at once and the viewer keeps running |
| loggers that follow new runs (`start_online_logger`, `start_tt_logger`) | a background process; its output goes to `~/SILAS/output/<name>_notebook.log` |
| results (`load_scan`, `load_full_frame`, `load_tt`, ...) | pandas tables and numpy arrays to work with in the notebook |

Long commands (`analyse_scan` of a whole scan, `reduce_full_frame`) block the notebook while they
run, as in the terminal; the interrupt button (■) stops them. Batch jobs (`full_frame_job`, the
`*_job.sh` scripts) are better for hours of work.

**Plots of your own** work as usual (`plt.plot(...)`): they appear below the cell as interactive
figures. Each figure appears once, below the cell that made it: later plots do not show the viewers
or earlier plots again. A figure stays usable until it is closed (`plt.close(fig)`, or
`plt.close("all")`, which also stops running viewers) or the kernel restarts.
MPI (`mpi=True`, the online viewers' MPI readers) is not used in a notebook: one process reads.

## How it works

- `silas/cli/jupyter_server.py` picks a free port and a token, writes `current.json`, puts
  `~/SILAS_jupyter`, `~/SILAS_py37` and SILAS in front of the Python path and
  starts JupyterLab; the kernels inherit that environment (and the SACLA module of the job).
- At every start the server writes the kernel definition
  `~/SILAS_jupyter/share/jupyter/kernels/python3/kernel.json`: the job's Python with
  `--ext=silas.cli.notebook`, so each kernel runs `silas.cli.notebook.start()`, which builds the
  session's namespace (`silas.cli.shell`) into the notebook.
- `silas.core.runner` runs the commands' scripts **inside the kernel** (their `main()`) when it is
  in a notebook, instead of a new process, so their output and figures come to the notebook.
- `silas.core.plotting` gives every figure the notebook's backend, ipympl (switching the backend
  would close running viewers). It puts each figure into a cell output once (`show()`, the viewers'
  start, and after every cell the figures that cell made), keeps shown figures open, and runs the
  viewers' update loop from a timer of the notebook instead of blocking.

Without ipympl the figures are images, and the viewers show one picture only and say so.

The server also points JupyterLab to its pages in `~/SILAS_jupyter/share/jupyter/lab`
(`JUPYTERLAB_DIR`): with the folder install they are not where JupyterLab looks by default.
