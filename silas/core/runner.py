"""Run scripts from the session, in this terminal or in a new one.

``target`` is either a file ("scripts/hello_sacla.py", relative to SILAS
or absolute) or a module name ("silas.online.logger", run with ``python -m``).

A launched script inherits $SILAS_PROFILE, $SILAS_CONFIG and (if set)
$SILAS_MODE, and reads the config file itself, so it sees exactly the
current config. Scripts should start with ``import silas``.

In a Jupyter notebook (silas.cli.notebook) a module is run INSIDE the kernel instead, by
calling its main(argv): its output and plots appear in the notebook (viewers keep running in
the background of the notebook, see silas.core.plotting). Only the loggers that follow new
runs forever (the new-terminal targets that are not viewers) are started as a background
process with a log file, so they do not block the notebook. MPI (launcher) is not used there.
"""
import importlib
import importlib.util
import os
import shlex
import shutil
import signal
import subprocess
import sys
from typing import List, Optional, Sequence, Tuple

from silas.core import bootstrap, sacla


def _resolve(target: str) -> Tuple[List[str], str]:
    """(python arguments, source file) for a file path or a module name."""
    looks_like_module = not target.endswith(".py") and os.sep not in target and "/" not in target
    if looks_like_module:
        spec = importlib.util.find_spec(target)
        if spec is None or not spec.origin:
            raise ImportError("no module named {!r}".format(target))
        return ["-m", target], spec.origin
    path = os.path.expanduser(target)
    if not os.path.isabs(path) and not os.path.exists(path):
        path = os.path.join(bootstrap.PACKAGE_ROOT, target)  # relative to SILAS
    if not os.path.exists(path):
        raise FileNotFoundError(target)
    path = os.path.abspath(path)
    return [path], path


OFFLINE_WORKERS = 6
"""Default number of worker processes of the offline (HPC) tools: analyse_scan, the offline
MPCCD viewer, make_runlog. Measured on the HPC (tests/bench_workers.py): reading
stops getting faster at 6 workers (the storage is the limit); bt_node asks for 6 cores."""


def available_cores() -> int:
    """Cores this process may use (the job's, on a compute node)."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def cap_workers(asked: int, what: str = "workers") -> int:
    """At most the job's cores (at least 1), with a message if fewer than asked."""
    cores = available_cores()
    n = max(1, min(int(asked), cores))
    if int(asked) > cores:
        print("{} {} asked, this job has {} cores: using {}".format(asked, what, cores, n))
    return n


VIEWERS = ("silas.online.mpccd_view", "silas.online.tt_view", "silas.online.scan_view",
           "silas.offline.fullframe_view")
"""Targets with a window: run inside the notebook kernel (their figures appear in the notebook)."""


def _run_in_notebook(target: str, args: Sequence[str], new_terminal: bool,
                     launcher: Optional[Sequence[str]]) -> Optional[int]:
    """run() in a Jupyter kernel (see the module text)."""
    import runpy

    py_args, source = _resolve(target)
    is_module = py_args[0] == "-m"
    if launcher:
        print("(notebook: one process, without {})".format(" ".join(launcher[:1])))
    if new_terminal and not (is_module and target in VIEWERS):
        logs = os.path.join(bootstrap.PACKAGE_ROOT, "output")
        if not os.path.isdir(logs):
            os.makedirs(logs)
        name = target.split(".")[-1] if is_module else os.path.splitext(os.path.basename(source))[0]
        log = os.path.join(logs, "{}_notebook.log".format(name))
        cmd = [sys.executable] + py_args + list(args)
        with open(log, "a") as fh:
            fh.write("\n=== {}\n".format(" ".join(cmd)))
            fh.flush()
            proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=_env(),
                                    cwd=bootstrap.PACKAGE_ROOT, start_new_session=True)
        print("started in the background (process {}): {}\noutput: {}\nstop it with: "
              "import os, signal; os.kill({}, signal.SIGINT)".format(proc.pid, " ".join(cmd), log, proc.pid))
        return None
    argv = [str(a) for a in args]
    try:
        if is_module:
            module = importlib.import_module(target)
            main = getattr(module, "main", None)
            if main is None:
                runpy.run_module(target, run_name="__main__", alter_sys=True)
            else:
                main(argv)
        else:
            saved = sys.argv
            sys.argv = [source] + argv
            try:
                runpy.run_path(source, run_name="__main__")
            finally:
                sys.argv = saved
    except SystemExit as err:
        code = err.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    except KeyboardInterrupt:
        print("stopped")
        return 1
    return 0


def worker_ignores_ctrl_c() -> None:
    """Initializer of multiprocessing pools: Ctrl-C reaches every process of the terminal;
    the workers ignore it and the main process stops them (pool.terminate()), so one Ctrl-C
    gives one clean stop instead of a traceback from every worker."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _call_here(cmd: List[str]) -> int:
    """Run cmd in this terminal and wait. Ctrl-C reaches the script too: it stops by itself
    (and cleans up), we only wait for it - up to 20 s, then it is terminated."""
    p = subprocess.Popen(cmd, env=_env(), cwd=bootstrap.PACKAGE_ROOT)
    try:
        return p.wait()
    except KeyboardInterrupt:
        try:
            return p.wait(timeout=20)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            p.terminate()
            return p.wait()


def _env() -> dict:
    env = dict(os.environ)
    pp = env.get("PYTHONPATH", "")
    if bootstrap.PACKAGE_ROOT not in pp.split(os.pathsep):
        env["PYTHONPATH"] = bootstrap.PACKAGE_ROOT + (os.pathsep + pp if pp else "")
    return env


def mpi_launcher(n_processes: int) -> Optional[List[str]]:
    """The profile's MPI command for n processes (e.g. ["mpirun", "-n", "5"]),
    None if mpi4py or the command is not available."""
    if importlib.util.find_spec("mpi4py") is None:
        return None
    cmd = bootstrap.profile().get("mpirun", "mpirun")
    cmd = list(cmd) if isinstance(cmd, list) else [cmd]
    if shutil.which(cmd[0]) is None:
        return None
    return cmd + ["-n", str(n_processes)]


def run(target: str, *args: str, new_terminal: bool = False, launcher: Optional[Sequence[str]] = None,
        execute: bool = False) -> Optional[int]:
    """Run a script file or module with ``args``.

    smoke mode: only smoke-test it (sacla_check), nothing is executed, unless
                execute=True (for runs that use no SACLA library, e.g. test data).
    live mode:  run it with this session's Python; with new_terminal=True in a
                new window (the profile's "terminal" command) without waiting.
    launcher:   command put in front, e.g. mpi_launcher(5) -> mpirun -n 5 python ...
    """
    py_args, source = _resolve(target)
    if not sacla.is_live() and not execute:
        return sacla.smoke_check(source)
    from silas.core import plotting

    if plotting.in_notebook():
        return _run_in_notebook(target, args, new_terminal, launcher)

    cmd = list(launcher or []) + [sys.executable] + py_args + list(args)  # type: List[str]
    if not new_terminal:
        return _call_here(cmd)

    terminal = bootstrap.profile().get("terminal")
    if not terminal:  # e.g. the HPC: no terminal program -> run here and wait
        print("profile {!r} has no 'terminal' command: running in this terminal (until it ends)".format(
            bootstrap.STATE["profile"]))
        return _call_here(cmd)
    # keep the window open after the script ends, so errors can be read
    shell = " ".join(shlex.quote(c) for c in cmd) + '; echo; read -p "[finished - press Enter to close]" x'
    subprocess.Popen(list(terminal) + ["bash", "-c", shell], env=_env(), cwd=bootstrap.PACKAGE_ROOT)
    print("started in a new terminal: {}".format(" ".join(cmd)))
    return None
