"""Detectors, point detectors and their aliases.

Two kinds of things can be read at SACLA:
  detectors        image detectors (MPCCD, other cameras): names from dbpy.read_detidlist
  point detectors  equipment / SyncDB signals (photodiodes, motors, status flags):
                   names from dbpy.read_equiplist, e.g. 'xfel_bl_3_tc_bm_1_pd/charge'

Both are stored in the config with an optional alias (and, for point
detectors, a unit). Everywhere a name is expected, the true name and the alias
both work (``resolve``). Data files always store true names; aliases are
applied when displaying (``display_name``, ``rename``), so an alias added later
also applies to data logged earlier.
"""
from typing import Any, Dict, Iterable, List, Optional

from silas.core.config import Config

KINDS = ("detectors", "point_detectors")


def _all_names(cfg: Config) -> Dict[str, str]:
    """{alias or true name: true name} over both kinds."""
    out = {}  # type: Dict[str, str]
    for kind in KINDS:
        for true_name, entry in cfg.get(kind, {}).items():
            out[true_name] = true_name
            alias = (entry or {}).get("alias")
            if alias:
                out[alias] = true_name
    return out


def _check_alias(cfg: Config, alias: Optional[str], true_name: str) -> None:
    if not alias:
        return
    if "/" in alias or alias.startswith("xfel_"):
        raise ValueError("alias {!r} looks like a true equipment name".format(alias))
    owner = _all_names(cfg).get(alias)
    if owner is not None and owner != true_name:
        raise ValueError("{!r} is already used for {!r}".format(alias, owner))


def _add(cfg: Config, kind: str, true_name: str, fields: Dict[str, Any]) -> None:
    entry = dict(cfg.get(kind, {}).get(true_name) or {})
    for key, value in fields.items():
        if value is not None:
            entry[key] = value
    _check_alias(cfg, entry.get("alias"), true_name)
    entries = dict(cfg.get(kind, {}))  # whole section: true names may contain dots
    entries[true_name] = entry
    cfg.set(kind, entries)


def add_detector(cfg: Config, det_id: str, alias: Optional[str] = None,
                 kind: Optional[str] = None) -> None:
    """Add (or update) an image detector, e.g.
    add_detector(cfg, "MPCCD-2N0-M02-001", alias="mpccd", kind="MPCCD")."""
    _add(cfg, "detectors", det_id, {"alias": alias, "kind": kind})


def add_point_detector(cfg: Config, equip_id: str, alias: Optional[str] = None,
                       unit: Optional[str] = None) -> None:
    """Add (or update) a point detector, e.g.
    add_point_detector(cfg, "xfel_bl_3_st_5_motor_user_1/position", alias="time_delay", unit="pulse")."""
    _add(cfg, "point_detectors", equip_id, {"alias": alias, "unit": unit})


def add_point_detectors(cfg: Config, equip_ids: Iterable[str]) -> None:
    """Add several point detectors without alias."""
    current = dict(cfg.get("point_detectors", {}))
    for equip_id in equip_ids:
        current.setdefault(equip_id, {})
    cfg.set("point_detectors", current)


def remove(cfg: Config, name: str) -> None:
    """Remove a detector or point detector (by true name or alias)."""
    true_name = resolve(cfg, name)
    for kind in KINDS:
        entries = dict(cfg.get(kind, {}))
        if true_name in entries:
            del entries[true_name]
            cfg.set(kind, entries)
            return


def set_alias(cfg: Config, name: str, alias: Optional[str]) -> None:
    """Set (alias=None: remove) the alias of a detector or point detector."""
    true_name = resolve(cfg, name)
    for kind in KINDS:
        entries = dict(cfg.get(kind, {}))
        if true_name in entries:
            entry = dict(entries[true_name] or {})
            if alias:
                _check_alias(cfg, alias, true_name)
                entry["alias"] = alias
            else:
                entry.pop("alias", None)
            entries[true_name] = entry
            cfg.set(kind, entries)
            return


def set_unit(cfg: Config, name: str, unit: Optional[str]) -> None:
    """Set (unit=None: remove) the unit of a point detector."""
    true_name = resolve(cfg, name)
    entries = dict(cfg.get("point_detectors", {}))
    if true_name not in entries:
        raise KeyError("{!r} is not a point detector".format(name))
    entry = dict(entries[true_name] or {})
    if unit:
        entry["unit"] = unit
    else:
        entry.pop("unit", None)
    entries[true_name] = entry
    cfg.set("point_detectors", entries)


def resolve(cfg: Config, name: str) -> str:
    """True name for an alias or true name. Names that are not in the config
    are returned unchanged (so any equipment name can be used directly)."""
    return _all_names(cfg).get(name, name)


def entry(cfg: Config, name: str) -> Dict[str, Any]:
    true_name = resolve(cfg, name)
    for kind in KINDS:
        found = cfg.get(kind, {}).get(true_name)
        if found is not None:
            return dict(found)
    return {}


def display_name(cfg: Config, name: str) -> str:
    """Alias if one is set, otherwise the true name."""
    return entry(cfg, name).get("alias") or resolve(cfg, name)


def label(cfg: Config, name: str) -> str:
    """Display name with unit, e.g. 'time_delay [pulse]' (for plots and tables)."""
    unit = entry(cfg, name).get("unit")
    base = display_name(cfg, name)
    return "{} [{}]".format(base, unit) if unit else base


def point_detector_ids(cfg: Config) -> List[str]:
    return list(cfg.get("point_detectors", {}))


def detector_ids(cfg: Config) -> List[str]:
    return list(cfg.get("detectors", {}))


def rename(cfg: Config, columns: Iterable[str]) -> Dict[str, str]:
    """{true name: display name} for e.g. ``df.rename(columns=rename(cfg, df.columns))``."""
    return {c: display_name(cfg, c) for c in columns}


def show(cfg: Config) -> None:
    """Table of all detectors and point detectors with alias and unit."""
    for kind in KINDS:
        entries = cfg.get(kind, {})
        print("{} ({}):".format(kind, len(entries)))
        for true_name, e in entries.items():
            e = e or {}
            extra = "  [{}]".format(e["unit"]) if e.get("unit") else ""
            extra += "  ({})".format(e["kind"]) if e.get("kind") else ""
            print("  {:<22s} {}{}".format(e.get("alias", "-"), true_name, extra))
