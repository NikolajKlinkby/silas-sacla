"""The session in a Jupyter notebook: every command of the terminal session, plots in the notebook.

The Jupyter server of SILAS (silas/cli/jupyter_server.py) gives every notebook a kernel that
loads this as an extension at its start, so start() is only needed by hand for another config:

    from silas.cli.notebook import start
    start()                                   # profile and config as for start_session.py (environment)
    start(profile="hpc")                      # another profile (before anything else of silas)
    start(config="config/other.json")         # another experiment config

or, as an IPython extension:  %load_ext silas.cli.notebook

After start() the notebook has the same names as the session (commands(), cfg, analyse_scan,
start_mpccd_viewer, ...). Commands that start a script run it inside the notebook's kernel
(silas.core.runner), so their output and plots appear in the notebook:

  * plots (analyse_scan, calibrate_tt, ...): interactive figures in the cell output (zoom, pan)
  * viewers (start_mpccd_viewer, start_tt_viewer, view_full_frame, ...): matplotlib's notebook
    widget (ipympl), with the keys and the mouse as in a window; the cell finishes at once and the
    viewer keeps running below it until q or closing the figure
  * loggers that follow new runs forever (start_online_logger, start_tt_logger): a background
    process with a log file in output/

On the HPC the Jupyter server runs as a batch job on a compute node (scripts/jupyter_job.sh,
bt_jupyter); see the docs page "Jupyter notebooks".
"""
import os
from typing import Any, Dict, Optional


def _after_cell(*args: Any) -> None:
    """After every cell (as matplotlib's inline backend does): figures made in the cell that are
    not in a cell output yet (e.g. your own plt.plot(...)) are shown below it, once, as interactive
    widgets. Figures already shown (running viewers, earlier plots) are not shown again."""
    import sys

    plt = sys.modules.get("matplotlib.pyplot")
    if plt is None:
        return
    from silas.core import plotting

    try:
        plotting.show(plt)
    except Exception as err:  # noqa: BLE001  never break the notebook over a plot
        print("(could not show a figure: {})".format(err))


def _notebook_pyplot() -> None:
    """matplotlib's notebook widget for every figure from the start (also your own plots), and
    plt.show() that shows only figures not shown yet (matplotlib's would show running viewers
    again)."""
    from silas.core import plotting

    plt = plotting.pyplot()
    if getattr(plt.show, "_silas", False):
        return

    def show(*args: Any, **kwargs: Any) -> None:
        plotting.show(plt)

    setattr(show, "_silas", True)
    setattr(plt, "show", show)


def _keep_backend() -> None:
    """In the notebook, matplotlib.use("Agg") (what the scripts call before saving a plot without
    showing it) is ignored: switching the backend closes every figure, also those of running
    viewers. The scripts close their own figures, so nothing extra appears in the notebook."""
    import matplotlib

    if getattr(matplotlib.use, "_silas", False):
        return
    original = matplotlib.use

    def use(backend: Any, *args: Any, **kwargs: Any) -> Any:
        from silas.core import plotting

        if str(backend).lower() == "agg" and plotting.in_notebook():
            return None
        return original(backend, *args, **kwargs)

    setattr(use, "_silas", True)
    setattr(matplotlib, "use", use)


def start(profile: Optional[str] = None, config: Optional[str] = None, mode: Optional[str] = None,
          quiet: bool = False) -> Dict[str, Any]:
    """Put the session's commands into this notebook (see the module text). profile / config /
    mode as start_session.py's options; they must be given before silas is used in the kernel
    (else restart the kernel). Returns the namespace."""
    import sys

    already = "silas" in sys.modules and getattr(sys.modules["silas"], "_bootstrap", None) is not None
    if profile:
        os.environ["SILAS_PROFILE"] = profile
    if config:
        os.environ["SILAS_CONFIG"] = os.path.abspath(os.path.expanduser(config))
    if mode:
        os.environ["SILAS_MODE"] = mode
    import silas  # noqa: F401  applies the profile (sys.path) on the first import
    from silas.cli import shell
    from silas.core import bootstrap
    from silas.core.config import Config

    if already and profile and bootstrap.STATE.get("profile") != profile:
        print("NOTE: silas was already loaded with profile {!r} in this kernel; restart the kernel to use "
              "{!r}".format(bootstrap.STATE.get("profile"), profile))
    cfg = Config()
    ns = shell.build_namespace(cfg)
    try:
        from IPython import get_ipython
    except ImportError:
        get_ipython = None  # type: ignore[assignment]
    ip = get_ipython() if get_ipython is not None else None
    if ip is not None:
        _keep_backend()
        _notebook_pyplot()
        ip.user_ns.update(ns)
        try:
            ip.events.unregister("post_execute", _after_cell)
        except ValueError:
            pass
        ip.events.register("post_execute", _after_cell)
    if not quiet:
        from silas.core import plotting

        print(shell.banner(cfg))
        print("notebook: plots and viewers {}".format(
            "as interactive figures (ipympl: zoom, pan, keys)" if plotting.has_widgets()
            else "as images: ipympl is missing (scripts/install_hpc_jupyter.sh)"))
    return ns


def load_ipython_extension(ip: Any) -> None:
    """%load_ext silas.cli.notebook"""
    start()
