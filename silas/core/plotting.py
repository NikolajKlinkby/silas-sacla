"""matplotlib windows wherever we run: a desktop window, or the browser (HPC).

pyplot(wanted) returns matplotlib.pyplot with a backend that can show something:

  * a window backend (TkAgg, Qt, GTK, WX) on the laptop and at the beamline;
  * WebAgg on the HPC: the figure is served as a web page by the compute node and opened
    in the laptop's browser through an SSH tunnel. Tk windows from the HPC compute nodes
    hang over X forwarding (even a plain Tk canvas), so the hpc profile uses
    WebAgg by default. WebAgg needs tornado, installed by scripts/install_hpc_packages.sh.

Which backend: the ``wanted`` one (e.g. display.backend of an MPCCD array), else
$SILAS_PLOT_BACKEND, else the profile's "plot_backend", else WebAgg on the hpc profile,
else the first window backend that works.

Viewers run their update loop with run_loop(plt, tick): with a window backend it calls
tick() and plt.pause() in turn; with WebAgg it calls tick() from the web server's event
loop (so key presses in the browser and updates happen in the same thread) and prints
the tunnel command and the link first.

In a Jupyter notebook (in_notebook(): the session's commands run inside a Jupyter kernel,
see silas.cli.notebook) the plots go into the notebook instead:
  * every figure is matplotlib's notebook widget (ipympl): zoom, pan, save, keys and mouse work
    in the notebook (without ipympl: images). A figure is put into the cell output once, by
    show() or run_loop(), never again by later plots; figures are not shown when made
    (interactive mode stays off in a notebook: ion() does nothing there), and close() keeps a
    shown widget open so it stays usable
  * the viewers (pyplot(interactive=True)): run_loop calls tick() from a timer of the notebook's event loop
    and returns at once, so the cell finishes and the viewer keeps running below it (q or
    closing the figure stops it; the viewer's cleanup runs then, see run_loop's on_stop).

Before importing pyplot the safe "Agg" backend is selected: the HPC's matplotlib has
WebAgg configured as default, and importing pyplot fails if tornado is missing.
"""
import getpass
import os
import socket
import traceback
import weakref
from typing import Any, Callable, List, Optional

GUI_BACKENDS = ("TkAgg", "QtAgg", "Qt5Agg", "GTK3Agg", "WXAgg")
NB_WIDGET = "module://ipympl.backend_nbagg"            # notebook: interactive figures (ipympl)
NB_INLINE = "module://matplotlib_inline.backend_inline"  # notebook: static images
_NB_TIMERS = []  # type: List[Any]
_NB_SHOWN = weakref.WeakSet()  # type: Any   # figures already put into a notebook cell
"""Timers of viewers running in a notebook (kept here so they are not garbage-collected)."""


def in_notebook() -> bool:
    """True inside a Jupyter kernel (not in the terminal IPython of the session).
    $SILAS_NOTEBOOK=0 switches the notebook behaviour off."""
    if os.environ.get("SILAS_NOTEBOOK") == "0":
        return False
    import sys

    if "ipykernel" not in sys.modules:
        return False
    try:
        from IPython import get_ipython
    except ImportError:
        return False
    shell = get_ipython()
    return shell is not None and getattr(shell, "kernel", None) is not None


def has_widgets() -> bool:
    """ipympl (matplotlib's notebook widget) can be used."""
    import importlib.util

    return importlib.util.find_spec("ipympl") is not None


def _notebook_pyplot(interactive: bool) -> Any:
    """pyplot in a notebook: ipympl if installed (for all figures: switching the backend would
    close the figures of running viewers), else the inline images; switched only when needed."""
    import matplotlib
    import matplotlib.pyplot as plt

    want = NB_WIDGET if has_widgets() else NB_INLINE
    if interactive and want != NB_WIDGET:
        print("(no ipympl here: the viewer shows a picture only; interactive figures come with the Jupyter "
              "packages, scripts/install_hpc_jupyter.sh)")
    if str(matplotlib.get_backend()).lower() != want.lower():
        try:
            plt.switch_backend(want)
        except Exception:  # noqa: BLE001  e.g. no matplotlib_inline: show() makes the images itself
            pass
    plt.ioff()        # figures appear where show() / run_loop() put them, once (see _nb_display)
    return plt


def ion(plt: Any) -> None:
    """plt.ion() outside a notebook (windows update by themselves). In a notebook nothing: with
    interactive mode on, ipympl would put every new figure into the cell where it is made."""
    if not in_notebook():
        plt.ion()


def _nb_display(plt: Any, figs: List[Any]) -> None:
    """Put the figures into the current cell output: the interactive widget (ipympl), else a
    PNG image. Figures already shown (e.g. a running viewer) are not shown again."""
    import io

    import matplotlib
    from IPython.display import Image, display

    widget = "ipympl" in str(matplotlib.get_backend())
    for fig in figs:
        if fig in _NB_SHOWN:
            continue
        _NB_SHOWN.add(fig)
        if widget:
            display(fig.canvas)
        else:
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=fig.dpi)
            display(Image(data=buf.getvalue()))


def close_all(plt: Any, figs: Any = None) -> None:
    """plt.close("all") outside a notebook. In a notebook (one kernel for everything, so other
    figures there belong to other plots and running viewers): only ``figs``, or with figs=None
    every figure that was not shown as a widget."""
    if not in_notebook():
        plt.close("all")
        return
    if figs is None:
        figs = [f for f in (plt.figure(n) for n in plt.get_fignums())
                if f not in _NB_SHOWN or not _widgets_on()]
    for fig in figs:
        plt.close(fig)


def close(plt: Any, fig: Any) -> None:
    """plt.close(fig), except for a figure shown as a notebook widget: that one stays open, so
    zoom, pan and its keys keep working (closing the widget's figure or restarting the kernel
    frees it)."""
    if in_notebook() and fig in _NB_SHOWN and _widgets_on():
        return
    plt.close(fig)


def _widgets_on() -> bool:
    import matplotlib

    return "ipympl" in str(matplotlib.get_backend())
NO_WINDOW = ("agg", "pdf", "svg", "ps", "pgf", "cairo", "template", "nbagg")
WEB = "WebAgg"
SSH_ALIAS = "sacla_HPC"          # the laptop's ssh alias for the HPC login node (docs: setup guide, step 1)
HPC_LOGIN = "xhpcfep.hpc.spring8.or.jp"   # the HPC login node, for laptops without the alias


def default_backend() -> Optional[str]:
    """$SILAS_PLOT_BACKEND, the profile's "plot_backend", or WebAgg on the hpc profile."""
    env = os.environ.get("SILAS_PLOT_BACKEND")
    if env:
        return env
    try:
        from silas.core import bootstrap

        prof = bootstrap.profile()
        if prof.get("plot_backend"):
            return str(prof["plot_backend"])
        if (bootstrap.STATE.get("profile") or bootstrap.active_profile_name()) == "hpc":
            return WEB
    except Exception:  # noqa: BLE001  no profile (e.g. a test): no preference
        pass
    return None


def is_web(plt: Any = None) -> bool:
    import matplotlib

    return matplotlib.get_backend().lower() == WEB.lower()


def pyplot(wanted: Optional[str] = None, interactive: bool = False) -> Any:
    """pyplot with a backend that can show figures (see the module docstring).
    interactive: the figures take keys / mouse and are updated (the viewers); only matters in a
    notebook (the widget instead of images). RuntimeError if none works."""
    if in_notebook():
        return _notebook_pyplot(interactive)
    import matplotlib

    try:  # the configured default, read without making matplotlib load it
        configured = str(dict.__getitem__(matplotlib.rcParams, "backend"))
    except Exception:  # noqa: BLE001
        configured = ""
    matplotlib.use("Agg")                 # pyplot can always be imported with this one
    import matplotlib.pyplot as plt

    first = wanted or default_backend()
    if first and first.lower() == WEB.lower():
        try:
            import tornado  # noqa: F401
        except ImportError:
            raise RuntimeError("the browser view (WebAgg) needs tornado: on the HPC run "
                               "bash ~/SILAS/scripts/install_hpc_packages.sh --go")
        matplotlib.rcParams["webagg.open_in_browser"] = False
        # only the node itself: the login node cannot reach other ports of the compute nodes
        # ("No route to host"), so the tunnel ends in an ssh login to the node (-J, see _web_banner),
        # which connects to 127.0.0.1. Other HPC machines cannot open the page.
        matplotlib.rcParams["webagg.address"] = "127.0.0.1"
        plt.switch_backend(WEB)
        print("matplotlib backend: {} (browser)".format(WEB))
        return plt

    candidates = []  # type: List[str]
    for name in [first or "", configured] + list(GUI_BACKENDS):
        if (name and name not in candidates and name.lower() not in NO_WINDOW
                and name.lower() != WEB.lower() and not name.startswith("module://")):
            candidates.append(name)
    errors = []  # type: List[str]
    for name in candidates:
        try:
            plt.switch_backend(name)
            plt.close(plt.figure())
            print("matplotlib backend:", name)
            return plt
        except Exception as err:  # noqa: BLE001  ImportError, missing display, ...
            errors.append("{}: {}".format(name, str(err).splitlines()[0] if str(err) else type(err).__name__))
    hint = ""
    if not os.environ.get("DISPLAY"):
        hint = ("\nno DISPLAY: no window can open here. Log in with 'ssh -Y' and get the compute node "
                "with X windows (bt_node asks for them when the login has a display; or bt_node -X).")
    raise RuntimeError("no matplotlib backend can open a window:\n  " + "\n  ".join(errors) + hint)


def tunnel_command(port: int, host: Optional[str] = None, user: Optional[str] = None,
                   jump: Optional[str] = None) -> str:
    """The laptop's ssh command to reach port ``port`` of this compute node: through the login
    node (-J sacla_HPC) to an ssh login on the node, which connects to the node's 127.0.0.1."""
    host = host or socket.gethostname()
    user = user or getpass.getuser()
    return "ssh -N -L {p}:127.0.0.1:{p} -J {a} {u}@{h}".format(p=port, a=jump or SSH_ALIAS, u=user, h=host)


def no_alias_note(user: Optional[str] = None) -> str:
    """For a laptop without the ssh alias: the same command with the login node by name."""
    return "(no 'sacla_HPC' in your ~/.ssh/config: write -J {}@{} instead of -J {})".format(
        user or getpass.getuser(), HPC_LOGIN, SSH_ALIAS)


def _web_banner(port: int) -> str:
    """What to do on the laptop. The command is on a line of its own (no indent) for copying."""
    line = "=" * 78
    return "\n".join([
        line,
        "The plots are served by {} on port {}.".format(socket.gethostname(), port),
        "On your LAPTOP (VPN on), in a new terminal, run (it prints nothing and stays open):",
        "",
        tunnel_command(port),
        "",
        no_alias_note(),
        "then open in your browser:   http://localhost:{}/".format(port),
        "(click on the figure for the keys to work; closing the browser tab is fine, the plots",
        " keep running and you can open the link again; Ctrl-C here stops)",
        line,
    ])


def _quiet_closed_sockets() -> None:
    """A closed browser tab must not stop the server: sending to a web socket that has just
    closed raises WebSocketClosedError (tornado) - ignore it, the page gets everything anew
    when it is opened again."""
    from matplotlib.backends.backend_webagg import WebAggApplication

    handler = getattr(WebAggApplication, "WebSocket", None)
    if handler is None or getattr(handler, "_bt_quiet", False):
        return
    import tornado.iostream
    import tornado.websocket

    closed = (tornado.websocket.WebSocketClosedError, tornado.iostream.StreamClosedError)

    def guard(method: Any) -> Any:
        def safe(self: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                return method(self, *args, **kwargs)
            except closed:
                return None
        return safe

    for name in ("send_json", "send_binary"):
        if hasattr(handler, name):
            setattr(handler, name, guard(getattr(handler, name)))
    handler._bt_quiet = True


def _web_start() -> Any:
    """Start the WebAgg server (not yet its loop); print how to reach it. Returns the IOLoop."""
    import tornado.ioloop
    from matplotlib.backends.backend_webagg import WebAggApplication

    _quiet_closed_sockets()
    WebAggApplication.initialize()
    port = getattr(WebAggApplication, "port", 8988)   # set by initialize() (not in the type stubs)
    print(_web_banner(int(port)), flush=True)
    return tornado.ioloop.IOLoop.current()


def _notebook_loop(plt: Any, tick: Callable[[], bool], period_s: float,
                   on_stop: Optional[Callable[[], None]]) -> bool:
    """run_loop in a notebook: tick() from a timer of the kernel's event loop (ipympl figures)."""
    import matplotlib

    figs = [plt.figure(n) for n in plt.get_fignums()]
    if not figs:
        if on_stop is not None:
            on_stop()
        return True
    if "ipympl" not in str(matplotlib.get_backend()):
        # static images only: draw once, then stop (a live viewer needs ipympl)
        print("this viewer needs interactive figures (ipympl): showing one picture only")
        try:
            tick()
        finally:
            show(plt)
            if on_stop is not None:
                on_stop()
        return True
    timer = figs[0].canvas.new_timer(interval=int(max(1.0, 1000.0 * period_s)))
    state = {"done": False}

    def callback() -> None:
        if state["done"]:
            return
        try:
            keep = bool(tick())
        except Exception:  # noqa: BLE001  show it and stop instead of repeating the error
            traceback.print_exc()
            keep = False
        if not keep:
            state["done"] = True
            timer.stop()
            if timer in _NB_TIMERS:
                _NB_TIMERS.remove(timer)
            if on_stop is not None:
                on_stop()

    timer.add_callback(callback)
    _NB_TIMERS.append(timer)
    timer.start()
    _nb_display(plt, figs)
    return True


def run_loop(plt: Any, tick: Callable[[], bool], period_s: float = 0.05,
             on_stop: Optional[Callable[[], None]] = None) -> bool:
    """Call tick() until it returns False, keeping the figures alive in between.

    Returns False when the loop has ended here (the caller cleans up, e.g. in a finally block);
    True when it goes on in the background (a notebook: the cell finishes, the viewer keeps
    running below it) - then on_stop() is called when it ends and the caller must NOT clean up
    now. Callers: ``detached = run_loop(plt, tick, 0.1, on_stop=stop)`` and ``if not detached:
    stop()``."""
    if in_notebook():
        return _notebook_loop(plt, tick, period_s, on_stop)
    if not is_web(plt):
        while tick():
            plt.pause(period_s)
        return False
    import tornado.ioloop
    from matplotlib.backends.backend_webagg import WebAggApplication

    loop = _web_start()

    import tornado.iostream
    import tornado.websocket

    closed = (tornado.websocket.WebSocketClosedError, tornado.iostream.StreamClosedError)

    def callback() -> None:
        try:
            if not tick():
                loop.stop()
        except closed:      # the browser tab went away while drawing: carry on
            pass
        except Exception:  # noqa: BLE001  show it and stop instead of looping on errors
            traceback.print_exc()
            loop.stop()

    tornado.ioloop.PeriodicCallback(callback, max(1.0, 1000.0 * period_s)).start()
    WebAggApplication.start()            # runs until loop.stop() or Ctrl-C
    return False


def show(plt: Any, fig: Any = None) -> None:
    """plt.show() that also works with WebAgg (prints the tunnel and the link, then serves
    until Ctrl-C). In a notebook: ``fig`` (or every open figure not shown yet) in the cell
    output, as an interactive widget (returns at once)."""
    if in_notebook():
        _nb_display(plt, [fig] if fig is not None else [plt.figure(n) for n in plt.get_fignums()])
        return
    if not is_web(plt):
        plt.show()
        return
    from matplotlib.backends.backend_webagg import WebAggApplication

    _web_start()
    WebAggApplication.start()
