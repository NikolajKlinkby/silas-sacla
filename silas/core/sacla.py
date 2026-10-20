"""The smoke / live switch for everything that needs a SACLA library.

live    functions and scripts run normally.
smoke   nothing SACLA-dependent runs. Instead the code is checked
        statically with ``sacla_check`` (sacla_local_api) (mypy against the
        API stubs).

Mark functions that need SACLA with ``@requires_sacla``::

    @requires_sacla
    def newest_tag(det_id):
        import olpy
        ...

The mode comes from the active profile; ``set_mode()`` overrides it for this
session and for scripts launched from it (via $SILAS_MODE).
"""
import functools
import inspect
import os
import subprocess
from typing import Any, Callable, Optional, TypeVar

from silas.core import bootstrap

F = TypeVar("F", bound=Callable[..., Any])


def mode() -> str:
    """Current mode: $SILAS_MODE if set, otherwise the active profile's mode."""
    override = os.environ.get("SILAS_MODE")
    if override:
        return override
    return bootstrap.profile().get("mode", "smoke")


def set_mode(new_mode: str) -> None:
    """Override the mode for this session (not saved). To change it
    permanently, edit "mode" of the profile in config/profiles.json."""
    if new_mode not in bootstrap.VALID_MODES:
        raise ValueError("mode must be one of {}".format(bootstrap.VALID_MODES))
    if new_mode == "live" and bootstrap.STATE["profile"] == "local":
        print("WARNING: live mode on the local profile. SACLA calls will really run here.")
    os.environ["SILAS_MODE"] = new_mode
    print("mode: {}".format(new_mode))


def is_live() -> bool:
    return mode() == "live"


def smoke_check(path: str) -> int:
    """Run sacla_check (mypy against the SACLA stubs) on ``path``.
    Needs the local SACLA environment (source sacla_local_api/sacla_env.sh before starting the
    session). Returns the exit code (0 = no problems)."""
    local = os.environ.get("SACLA_LOCAL")
    if not local:
        print("smoke test not possible: $SACLA_LOCAL is not set "
              "(source sacla_local_api/sacla_env.sh before starting the session)")
        return 2
    checker = os.path.join(local, "scripts", "sacla_check.sh")
    print("[smoke] sacla_check {}".format(path))
    return subprocess.call(["bash", checker, path])


def requires_sacla(func: F) -> F:
    """Decorator: in smoke mode the function is not called; the file that defines
    it is smoke-tested instead, and None is returned."""
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if is_live():
            return func(*args, **kwargs)
        source: Optional[str] = inspect.getsourcefile(func)
        print("[smoke] {}() not executed (mode = smoke)".format(func.__name__))
        if source:
            smoke_check(source)
        return None
    wrapper.requires_sacla = True  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]
