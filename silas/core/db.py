"""dbpy access shared by online (DAQ-USER-LAN) and offline (HPC-LAN).

These run/tag based dbpy functions work on both networks. The *_current
variants (last ~300 tags only) are left to silas.online.

Conventions fixed here once, so callers never have to think about them:
  * all dbpy arguments are positional and tag lists are tuples
  * the high tag is always taken from the run (never hard-coded)
  * dbpy is imported inside each function (not at module level): this module
    imports anywhere (also on the HPC login node, where dbpy refuses to load),
    and mypy / sacla_check still checks every call against the stubs
"""
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

RUN_STATUS = {-1: "not yet", 0: "stopped", 1: "paused", 2: "running"}

_CHUNK = 5000  # tags per read_syncdatalist call


def api_error() -> Any:
    """dbpy.APIError (for ``except db.api_error():``)."""
    import dbpy

    return dbpy.APIError


# ---------------------------------------------------------------- runs
def newest_run(bl: int) -> int:
    import dbpy

    return int(dbpy.read_runnumber_newest(bl))


def run_status(bl: int, run: int) -> int:
    """-1 not yet existing, 0 stopped, 1 paused, 2 running."""
    import dbpy

    return int(dbpy.read_runstatus(bl, run))


def run_info(bl: int, run: int) -> Dict[str, Any]:
    """dbpy.read_runinfo plus 'run' and 'beamline'."""
    import dbpy

    info = dict(dbpy.read_runinfo(bl, run))
    info["run"] = run
    info["beamline"] = bl
    return info


def high_tag(bl: int, run: int) -> int:
    import dbpy

    return int(dbpy.read_hightagnumber(bl, run))


def tag_range(bl: int, run: int) -> Tuple[int, int]:
    """(first tag, last tag) of the run."""
    import dbpy

    _, start = dbpy.read_start_tagnumber(bl, run)
    _, end = dbpy.read_end_tagnumber(bl, run)
    return int(start), int(end)


def tags(bl: int, run: int) -> Tuple[int, ...]:
    """All tags of the run."""
    import dbpy

    return tuple(int(t) for t in dbpy.read_taglist_byrun(bl, run))


def run_detectors(bl: int, run: int) -> Tuple[str, ...]:
    """Image detectors (MPCCD, cameras) recorded in the run."""
    import dbpy

    return tuple(dbpy.read_detidlist(bl, run))


def equipment() -> Tuple[str, ...]:
    """All point detectors / equipment IDs known to the SyncDB."""
    import dbpy

    return tuple(dbpy.read_equiplist())


def newest_tag(equip_id: str) -> Tuple[int, int]:
    """(high tag, tag) of the newest tag recorded for ``equip_id``."""
    import dbpy

    hi, tag = dbpy.read_tagnumber_newest(equip_id)
    return int(hi), int(tag)


def timestamp(taghi: int, tag: int, equip_id: str) -> float:
    """Unix time of a tag."""
    import dbpy

    return float(dbpy.read_timestamp_fromtag(taghi, tag, equip_id))


# ---------------------------------------------------------------- point detectors
def _chunks(seq: Sequence[int], n: int) -> Iterable[Tuple[int, ...]]:
    for i in range(0, len(seq), n):
        yield tuple(int(t) for t in seq[i:i + n])


def read_point(equip_id: str, taghi: int, tag_list: Sequence[int],
               errors: str = "raise") -> List[float]:
    """Values of one point detector for ``tag_list`` (same order), as float.

    errors="raise": a dbpy.APIError is passed on.
    errors="nan":   the affected chunk becomes NaN (a message is printed)."""
    import dbpy

    out = []  # type: List[float]
    for chunk in _chunks(list(tag_list), _CHUNK):
        try:
            out.extend(float(v) for v in dbpy.read_syncdatalist_float(equip_id, taghi, chunk))
        except dbpy.APIError as err:
            if errors != "nan":
                raise
            print("read_point {}: {} -> NaN for {} tags".format(equip_id, err.args, len(chunk)))
            out.extend([math.nan] * len(chunk))
    return out


def read_points(equip_ids: Iterable[str], taghi: int, tag_list: Sequence[int],
                errors: str = "nan") -> Any:
    """pandas DataFrame (index = tag, one column per point detector, true names)."""
    import pandas as pd

    tag_list = list(tag_list)
    data = {e: read_point(e, taghi, tag_list, errors=errors) for e in equip_ids}
    return pd.DataFrame(data, index=pd.Index(tag_list, name="tag"))


def read_run_points(bl: int, run: int, equip_ids: Iterable[str],
                    tag_list: Optional[Sequence[int]] = None, errors: str = "nan") -> Any:
    """read_points for (all or some) tags of a run, high tag from the run."""
    return read_points(equip_ids, high_tag(bl, run), tag_list if tag_list is not None else tags(bl, run),
                       errors=errors)
