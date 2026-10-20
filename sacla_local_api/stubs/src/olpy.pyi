"""SACLA OnlineUserAPI (olpy 1.2.1) – online detector readout.

Typical loop::

    det_id = olpy.read_detidlist()[0]
    reader = olpy.StorageReader(det_id)
    buf = olpy.StorageBuffer(reader)
    tag = reader.collect(buf, olpy.NEWESTTAG)
    img = buf.read_det_data(0)          # float32 (ysize, xsize)

On the HPC "online" module / locally with OL_SIMPATH set this runs in
simulation mode and reads the files in $OL_SIMPATH.
Hand-written stub generated from OnlineUserAPI/1.2.1/python/olpy.cpp.
"""
from typing import TypedDict, final

import numpy as np
from numpy.typing import NDArray

class APIError(Exception):
    """Raised on any API failure. ``args == (error_code, error_name)``;
    compare ``args[0]`` with the ``OL_ERR_*`` constants."""
    args: tuple[int, str]

class _DetInfo(TypedDict, total=False):
    """Type-only: dictionary returned by StorageBuffer.read_det_info().
    The mp_* keys exist only for MPCCD detectors and only for index 0."""
    run_number: int
    tag_number: int
    xsize: int
    """detector width in pixels (usually the smaller side)"""
    ysize: int
    """detector height in pixels"""
    mp_absgain: float
    mp_posx: float
    mp_posy: float
    mp_posz: float
    mp_rotationangle: float
    mp_stagedirection: float
    mp_shiftweight: float
    mp_pixelsize_x: float
    """pixel size in x (micrometre per pixel)"""
    mp_pixelsize_y: float
    """pixel size in y (micrometre per pixel)"""
    mp_typeid_sensor: int
    mp_typeid_readoutboard: int
    mp_invalidflag: int
    mp_temperature: float

@final
class StorageReader:
    """Connection to the online storage of one detector."""
    def __init__(self, det_id: str, /) -> None:
        """Connect to detector ``det_id`` (an entry of ``olpy.read_detidlist()``).
        Raises APIError (e.g. OL_ERR_CONNECT, OL_ERR_SIMU_FILEERR_*)."""
    def collect(self, buffer: StorageBuffer, tag_lo: int = -1, /) -> int:
        """Fetch the event data of tag ``tag_lo`` (lower 32 bit) into ``buffer``.
        ``tag_lo = olpy.NEWESTTAG`` (default, -1) fetches the newest tag.
        Returns the tag number actually collected (the C docs say None, but the
        binding returns the tag). Raises APIError, e.g. OL_ERR_TAGDATAGONE when
        the tag is no longer in the buffer."""

@final
class StorageBuffer:
    """Read buffer for one StorageReader; reused for every collect()."""
    def __init__(self, reader: StorageReader, /) -> None:
        """Allocate a buffer sized for ``reader``'s detector."""
    def read_det_num_index(self) -> int:
        """Number of detector images (sections) held in the buffer."""
    def read_det_info(self, index: int, /) -> _DetInfo:
        """Detector/event information for image ``index`` (see _DetInfo keys).
        Raises APIError OL_ERR_WRONGDATAIDX if index >= read_det_num_index()."""
    def read_det_data(self, index: int, /) -> NDArray[np.float32]:
        """Detector image ``index`` as a float32 array of shape (ysize, xsize)."""

def read_detidlist() -> tuple[str, ...]:
    """All detector IDs available (in simulation mode: those in DetectorsConf.lst)."""

def get_api_version() -> tuple[int, int, int]:
    """(major, minor, patch) of the olpy API."""
