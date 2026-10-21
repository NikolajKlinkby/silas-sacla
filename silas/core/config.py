"""Persistent experiment config of the active profile (config/<profile>.json).

Editable live from the session. Every change is written to the file immediately
(atomically), and the file is re-read whenever another process (a viewer, a
script, another session) changed it, so nothing is overwritten with stale data:
  * the next session of this profile starts with the same config, and
  * a script started in another terminal reads the current config.

Keys are dotted paths into the JSON tree::

    cfg.get("beamline")
    cfg.set("first_run", 1234400)
    cfg.show("point_detectors")

Detectors and point detectors are best added with the helpers in
silas.core.names (add_detector, add_point_detector, ...), which check aliases.
"""
import json
import os
from typing import Any, Dict, List, Optional

from silas.core import bootstrap

_MISSING = object()

DEFAULT_BEAMLINE = 3
DEFAULT_EV_PER_ABSGAIN_ADU = 3.65   # eV per electron-hole pair in Si (silas.core.energy)


def default_config(profile_name: str) -> Dict[str, Any]:
    return {
        "version": 2,
        "profile": profile_name,
        "beamline": DEFAULT_BEAMLINE,
        "first_run": None,
        "last_run": None,
        "xfel_shutter": None,  # alias of the point detector that is the X-FEL shutter
        # photon energy (keV) used by all analysis; null: taken from the run record (dbpy)
        "photon_energy_keV": None,
        "paths": {
            "output": os.path.join(bootstrap.PACKAGE_ROOT, "output", profile_name),
        },
        # image detectors (dbpy.read_detidlist names): {det_id: {"alias": .., "kind": ..}}
        "detectors": {},
        # point detectors / equipment (dbpy.read_equiplist names): {equip_id: {"alias": .., "unit": ..}}
        "point_detectors": {},
        # energy calibration for MPCCD arrays added later (each array keeps its own copy)
        "calibration": {"ev_per_absgain_adu": DEFAULT_EV_PER_ABSGAIN_ADU},
    }


class Config(object):
    """The config file as a nested dict with get/set/delete by dotted key."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or bootstrap.config_path()
        self.data = {}  # type: Dict[str, Any]
        self._stamp = None  # type: Optional[tuple]
        self.reload()

    # ------------------------------------------------------------------ file
    def reload(self) -> None:
        """Re-read the file (e.g. after another process changed it).
        Creates it with defaults if it does not exist; adds missing top-level keys."""
        name = bootstrap.STATE["profile"] or bootstrap.active_profile_name()
        defaults = default_config(name)
        if os.path.exists(self.path):
            self.data = bootstrap.read_json(self.path)
            self._stamp = self.file_stamp()
            missing = [k for k in defaults if k not in self.data]
            for k in missing:
                self.data[k] = defaults[k]
            if missing:
                self.save()
        else:   # the rough default; setup_config() makes a full one (silas.core.setup_config)
            self.data = defaults
            self.save()

    def save(self) -> None:
        bootstrap.write_json(self.path, self.data)
        self._stamp = self.file_stamp()

    def file_stamp(self) -> Optional[tuple]:
        """(inode, size, mtime) of the file: changes on every save (each save
        replaces the file, so the inode changes even within one second)."""
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_ino, st.st_size, st.st_mtime_ns)

    def _sync(self) -> None:
        """Re-read the file if another process changed it since we read/wrote it."""
        if self.file_stamp() != self._stamp:
            self.reload()

    # ------------------------------------------------------------------ access
    @staticmethod
    def _split(key: str) -> List[str]:
        parts = [p for p in key.split(".") if p]
        if not parts:
            raise KeyError("empty key")
        return parts

    def get(self, key: str, default: Any = _MISSING) -> Any:
        self._sync()
        node = self.data  # type: Any
        for part in self._split(key):
            if not isinstance(node, dict) or part not in node:
                if default is _MISSING:
                    raise KeyError(key)
                return default
            node = node[part]
        return node

    def set(self, key: str, value: Any, save: bool = True) -> None:
        """Set ``key`` (creating intermediate sections) and save."""
        json.dumps(value)  # fail now, not at save time, if value is not JSON-able
        self._sync()
        parts = self._split(key)
        node = self.data
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
        if save:
            self.save()

    def delete(self, key: str, save: bool = True) -> None:
        self._sync()
        parts = self._split(key)
        node = self.get(".".join(parts[:-1])) if len(parts) > 1 else self.data
        del node[parts[-1]]
        if save:
            self.save()

    def show(self, key: Optional[str] = None) -> None:
        """Pretty-print the whole config or one section."""
        self._sync()
        value = self.data if key is None else self.get(key)
        print(json.dumps(value, indent=2))

    # ------------------------------------------------------------------ shortcuts
    @property
    def beamline(self) -> int:
        return int(self.get("beamline", DEFAULT_BEAMLINE))

    def output_dir(self, create: bool = True) -> str:
        """The profile's output folder (created if needed)."""
        path = os.path.abspath(os.path.expanduser(self.get("paths.output")))
        if create and not os.path.isdir(path):
            os.makedirs(path)
        return path

    # ------------------------------------------------------------------ profiles
    @property
    def profile_name(self) -> str:
        return bootstrap.STATE["profile"] or bootstrap.active_profile_name()

    @property
    def profile(self) -> Dict[str, Any]:
        """Definition of the active profile (from profiles.json)."""
        return bootstrap.profile(self.profile_name)

    def use_profile(self, name: str) -> None:
        """Make ``name`` the default profile for the NEXT session (sys.path is
        only set at start-up, so restart the session to switch)."""
        data = bootstrap.load_profiles()
        if name not in data["profiles"]:
            raise ValueError("unknown profile {!r}".format(name))
        data["default"] = name
        bootstrap.save_profiles(data)
        print("default profile is now {!r}; restart the session to apply it".format(name))

    def __repr__(self) -> str:
        return "Config({!r}, profile={!r})".format(self.path, self.profile_name)
