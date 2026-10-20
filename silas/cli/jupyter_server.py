"""A Jupyter server for SILAS notebooks on an HPC compute node, reached from the laptop.

    qsub ~/SILAS/scripts/jupyter_job.sh       # batch job (bt_jupyter on the login node does it)
    python -m silas.cli.jupyter_server            # on a compute node you are on (bt_node), in the foreground
    python -m silas.cli.jupyter_server --classic  # the classic notebook instead of JupyterLab

It
  1. picks a free port on this node (from 8890) and a random token (the password of the link),
  2. writes them to ~/SILAS/output/jupyter/current.json (only you can read it; the
     laptop's scripts/hpc_jupyter.sh reads it to open the tunnel) and prints the ssh tunnel
     command and the link,
  3. starts JupyterLab with the Jupyter packages of ~/SILAS_jupyter
     (scripts/install_hpc_jupyter.sh) in front of the SACLA Python's own, listening only on
     this node (127.0.0.1): the tunnel goes through the login node to an ssh login on the node,
     as for the plots in the browser (silas.core.plotting).

The notebooks' kernels inherit the environment: the SACLA module, $SILAS_PROFILE (hpc) and
the paths, so `from silas.cli.notebook import start; start()` in a notebook gives the session.
"""
import argparse
import datetime
import getpass
import json
import os
import secrets
import socket
import sys
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # SILAS
JUPYTER_DIR = os.path.expanduser(os.environ.get("SILAS_JUPYTER_DIR", "~/SILAS_jupyter"))
EXTRA_DIRS = [JUPYTER_DIR, os.path.expanduser("~/SILAS_py37")]
FIRST_PORT = 8890


def free_port(first: int = FIRST_PORT, tries: int = 200) -> int:
    """The first port from ``first`` on that nobody listens to on this node."""
    for port in range(first, first + tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("no free port in {}-{}".format(first, first + tries - 1))


def info_dir() -> str:
    path = os.path.join(ROOT, "output", "jupyter")
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def environment() -> Dict[str, str]:
    """The server's (and so the kernels') environment: the Jupyter packages first on PYTHONPATH,
    then SILAS's extra packages and SILAS itself; Jupyter's data and config there."""
    env = dict(os.environ)
    paths = [p for p in EXTRA_DIRS if os.path.isdir(p)] + [ROOT]
    old = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p and p not in paths]
    env["PYTHONPATH"] = os.pathsep.join(paths + old)
    share = os.path.join(JUPYTER_DIR, "share", "jupyter")
    etc = os.path.join(JUPYTER_DIR, "etc", "jupyter")
    env["JUPYTER_PATH"] = os.pathsep.join([share] + [p for p in env.get("JUPYTER_PATH", "").split(os.pathsep) if p])
    env["JUPYTER_CONFIG_PATH"] = os.pathsep.join(
        [etc] + [p for p in env.get("JUPYTER_CONFIG_PATH", "").split(os.pathsep) if p])
    # JupyterLab's own pages (static/, schemas/, themes/): with pip --target they are in the folder,
    # not in sys.prefix/share/jupyter/lab where JupyterLab looks by default
    env["JUPYTERLAB_DIR"] = lab_dir()
    env.setdefault("SILAS_PROFILE", "hpc")
    return env


def write_kernelspec() -> str:
    """The notebooks' default kernel ("python3", found first on JUPYTER_PATH): this Python with the
    session loaded at the start (IPython extension silas.cli.notebook), so every new notebook has
    the session's commands (show_scans, analyse_scan, ...) without a first cell. Written at every server
    start, so it follows the Python of the job. Returns the kernel folder."""
    folder = os.path.join(JUPYTER_DIR, "share", "jupyter", "kernels", "python3")
    os.makedirs(folder, exist_ok=True)
    spec = {
        "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}",
                 "--ext=silas.cli.notebook"],
        "display_name": "SILAS (Python {}.{})".format(*sys.version_info[:2]),
        "language": "python",
        "metadata": {"debugger": False},
    }
    with open(os.path.join(folder, "kernel.json"), "w") as fh:
        json.dump(spec, fh, indent=1)
    return folder


def lab_dir() -> str:
    """JupyterLab's application folder in the Jupyter package folder."""
    return os.path.join(JUPYTER_DIR, "share", "jupyter", "lab")


def tunnel_no_alias(port: int, host: str, user: str) -> str:
    """The same tunnel for a laptop without the sacla_HPC alias in ~/.ssh/config."""
    from silas.core.plotting import HPC_LOGIN, tunnel_command

    return tunnel_command(port, host, user, jump="{}@{}".format(user, HPC_LOGIN))


def tunnel(port: int, host: str, user: str) -> str:
    from silas.core.plotting import tunnel_command

    return tunnel_command(port, host, user)


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Jupyter server for SILAS notebooks (HPC compute node)")
    ap.add_argument("--port", type=int, help="port (default: the first free one from {})".format(FIRST_PORT))
    ap.add_argument("--root", default=os.path.expanduser("~"), help="folder the notebooks are browsed from "
                                                                     "(default: your home)")
    ap.add_argument("--classic", action="store_true", help="the classic notebook instead of JupyterLab")
    args = ap.parse_args(argv)

    if not os.path.isdir(JUPYTER_DIR):
        print("no {}: install the Jupyter packages first (laptop: bash scripts/fetch_jupyter_packages.sh, sync; "
              "HPC: bash ~/SILAS/scripts/install_hpc_jupyter.sh --go)".format(JUPYTER_DIR), flush=True)
        sys.exit(1)
    host = socket.gethostname().split(".")[0]
    user = getpass.getuser()
    port = int(args.port) if args.port else free_port()
    token = secrets.token_hex(24)
    url = "http://localhost:{}/{}?token={}".format(port, "tree" if args.classic else "lab", token)
    info = {"host": host, "user": user, "port": port, "token": token, "url": url,
            "tunnel": tunnel(port, host, user), "job": os.environ.get("PBS_JOBID", ""),
            "tunnel_no_alias": tunnel_no_alias(port, host, user),
            "pid": os.getpid(), "root": os.path.abspath(args.root), "app": "notebook" if args.classic else "lab",
            "started": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    folder = info_dir()
    for name in ("current.json", "{}_{}.json".format(host, port)):
        path = os.path.join(folder, name)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)      # the token: only for you
        with os.fdopen(fd, "w") as fh:
            json.dump(info, fh, indent=1)
        os.chmod(path, 0o600)
    line = "=" * 78
    print("\n".join([
        line,
        "Jupyter for SILAS on {} (port {}), job {}".format(host, port, info["job"] or "-"),
        "On your LAPTOP (VPN on), either",
        "",
        "    bash scripts/hpc_jupyter.sh          (in your SILAS folder: tunnel + link)",
        "",
        "or in a new terminal (it prints nothing and stays open):",
        "",
        info["tunnel"],
        "",
        "(without the sacla_HPC alias in ~/.ssh/config:)",
        info["tunnel_no_alias"],
        "",
        "and then open in your browser:",
        "",
        url,
        "",
        "Notebooks start with the session loaded (kernel 'SILAS'): commands() lists the commands",
        line,
    ]), flush=True)
    app = "notebook" if args.classic else "jupyterlab"
    cmd = [sys.executable, "-m", app, "--no-browser", "--ip=127.0.0.1", "--port={}".format(port),
           "--port-retries=0"]
    if args.classic:
        cmd += ["--NotebookApp.token={}".format(token), "--NotebookApp.notebook_dir={}".format(info["root"])]
    else:
        cmd += ["--ServerApp.token={}".format(token), "--ServerApp.root_dir={}".format(info["root"]),
                "--LabApp.app_dir={}".format(lab_dir())]
        if not os.path.isdir(os.path.join(lab_dir(), "static")):
            print("WARNING: no JupyterLab pages in {} (static/ missing): the browser will show an error. "
                  "Reinstall: bash ~/SILAS/scripts/install_hpc_jupyter.sh --go".format(lab_dir()), flush=True)
    write_kernelspec()
    os.chdir(info["root"])
    os.execvpe(sys.executable, cmd, environment())


if __name__ == "__main__":
    main()
