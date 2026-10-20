"""The interactive session: IPython if available, otherwise the plain Python
prompt (code.interact) with tab completion.

Start it with start_session.py (which applies the profile first), not directly.
"""
import code
import os
import sys
from typing import Any, Dict

from silas.cli import commands as _commands
from silas.core import bootstrap, runner, sacla
from silas.core.config import Config


def build_namespace(cfg: Config) -> Dict[str, Any]:
    """Everything the session exposes. New capabilities get added here."""
    import silas

    ns = {
        "silas": silas,
        "cfg": cfg,
        "run": runner.run,
        "smoke_check": sacla.smoke_check,
        "mode": sacla.mode,
        "set_mode": sacla.set_mode,
        "requires_sacla": sacla.requires_sacla,
    }  # type: Dict[str, Any]
    ns.update(_commands.build(cfg))

    def session_info() -> None:
        """Profile, mode, config file and sys.path changes of this session."""
        print(banner(cfg))

    def commands() -> None:
        """List everything available in this session."""
        _print_commands(ns)

    ns["session_info"] = session_info
    ns["commands"] = commands
    return ns


def _print_commands(ns: Dict[str, Any]) -> None:
    print("Available in this session:")
    for name in sorted(n for n in ns if not n.startswith("_")):
        obj = ns[name]
        doc = (getattr(obj, "__doc__", None) or "").strip().split("\n")[0]
        print("  {:<20s} {}".format(name, doc))


def banner(cfg: Config) -> str:
    st = bootstrap.STATE
    lines = [
        "SILAS session",
        "  profile : {}  ({})".format(st["profile"], cfg.profile.get("description", "")),
        "  mode    : {}".format(sacla.mode()),
        "  config  : {}".format(cfg.path),
        "  profiles: {}".format(bootstrap.PROFILES_FILE),
        "  beamline: {}   first_run: {}   output: {}".format(
            cfg.beamline, cfg.get("first_run", None), cfg.get("paths.output")),
        "  python  : {} ({})".format(sys.version.split()[0], sys.executable),
    ]
    for p in st["inserted"]:
        lines.append("  sys.path+ {}".format(p))
    for p in st["missing"]:
        lines.append("  MISSING   {}  (not added)".format(p))
    if sacla.mode() == "smoke" and not os.environ.get("SACLA_LOCAL"):
        lines.append("  NOTE: smoke tests need the local SACLA environment: source sacla_local_api/sacla_env.sh first")
    lines.append("Type commands() for the list of commands, cfg.show() for the config.")
    return "\n".join(lines)


def start(use_ipython: bool = True) -> None:
    cfg = Config()
    ns = build_namespace(cfg)
    text = banner(cfg)

    if use_ipython:
        try:
            import IPython  # looked up AFTER the profile's sys.path entries were added
        except ImportError:
            print("(IPython not available, using the plain Python prompt)")
        else:
            print(text)
            IPython.start_ipython(argv=[], user_ns=ns)
            return

    try:  # tab completion for the plain prompt
        import readline
        import rlcompleter

        readline.set_completer(rlcompleter.Completer(ns).complete)
        readline.parse_and_bind("tab: complete")
    except ImportError:
        pass
    code.interact(banner=text, local=ns, exitmsg="")
