"""SACLA DataAccessUserAPI – storage (detector data) access, stpy 1.3.0.

Typical use (offline, on an HPC compute node)::

    reader = stpy.StorageReader("MPCCD-8-2-001-1", 3, (run,))
    buf = stpy.StorageBuffer(reader)
    for tag in dbpy.read_taglist_byrun(3, run):
        reader.collect(buf, tag)
        img = buf.read_det_data(0)

Only works where the SACLA storage is reachable (import is blocked on the
login node fep01). Hand-written stub from DataAccessUserAPI/1.3.0/python/stpy.cpp.
"""
from typing import TypedDict, final, overload

import numpy as np
from numpy.typing import NDArray

class APIError(Exception):
    """Raised on any API failure. ``args == (error_code, error_name)``;
    compare ``args[0]`` with the ``DA_ERR_*`` / ``ST_ERR_*`` / ``SE_ERR_*`` constants."""
    args: tuple[int, str]

class _DetInfo(TypedDict, total=False):
    """Type-only: dictionary returned by StorageBuffer.read_det_info().
    NB: the C help text says ``mp_pixelsize`` but the binding sets
    ``mp_pixelsizex`` / ``mp_pixelsizey``."""
    xsize: int
    ysize: int
    xscale: float
    """scale of x axis (m)"""
    yscale: float
    """scale of y axis (m)"""
    xoffset: float
    """offset of x axis (m)"""
    yoffset: float
    """offset of y axis (m)"""
    mp_absgain: float
    mp_posx: float
    mp_posy: float
    mp_posz: float
    mp_rotationangle: float
    mp_stagedirection: float
    mp_shiftweight: float
    mp_pixelsizex: float
    mp_pixelsizey: float
    mp_manipulator_shift: float
    mp_typeid_sensor: int
    mp_typeid_readoutboard: int
    mp_invalidflag: int
    mp_temperature: float

@final
class StorageReader:
    """Reader for one detector over a set of runs."""
    @overload
    def __init__(self) -> None:
        """Empty reader, to be filled with unmarshal()."""
    @overload
    def __init__(self, det_id: str, bl: int, runlist: tuple[int, ...], /) -> None:
        """Reader for detector ``det_id`` on beamline ``bl`` for the runs in
        ``runlist`` (must be a tuple of int, a list raises TypeError)."""
    def collect(self, buffer: StorageBuffer, tag_lo: int, /) -> None:
        """Read the data of tag ``tag_lo`` into ``buffer``."""
    def marshal(self, filepath: str, /) -> None:
        """Save the reader state to ``filepath`` (to share between processes)."""
    def unmarshal(self, filepath: str, /) -> None:
        """Restore a reader state saved with marshal()."""

@final
class StorageBuffer:
    """Read buffer belonging to one StorageReader."""
    def __init__(self, reader: StorageReader, /) -> None: ...
    def read_det_num_index(self) -> int:
        """Number of detector images (sections) in the buffer."""
    def read_det_info(self, index: int, /) -> _DetInfo:
        """Detector information for image ``index`` (see _DetInfo keys)."""
    def read_det_data(self, index: int, /) -> NDArray[np.float32]:
        """Image ``index`` as float32 (converted if the raw data are not float)."""

def get_api_version() -> tuple[int, int, int]:
    """(major, minor, patch) of the stpy API."""
