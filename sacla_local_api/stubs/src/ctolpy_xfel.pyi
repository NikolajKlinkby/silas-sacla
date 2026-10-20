"""SACLA CITIUSOnlineUserAPI, ctolpy_xfel 1.1.0 – online CITIUS readout.

Typical loop::

    buf = ctolpy_xfel.CtrlBuffer(connect_id)
    sids = buf.read_sensoridlist()
    bx, by, w, h = buf.read_binninginfo()
    images = np.empty((len(sids), h, w), np.float32)
    info = buf.collect_data(images, sids, ctolpy_xfel.NEWEST)

Simulation mode reads the file in $CTOL_SIMPATH (not set up locally).
Images/masks are written into caller-allocated arrays.
Hand-written stub from CITIUSOnlineUserAPI/1.1.0/python/ctolpy_xfel.cpp.
"""
from typing import TypedDict, final

import numpy as np
from numpy.typing import NDArray

class APIError(Exception):
    """Raised on any API failure. ``args == (error_code, error_name)``; compare
    ``args[0]`` with the ``CTOL_ERR_*`` constants, e.g.
    CTOL_ERR_GETDATA_CMD_RESULT_TIMEOUT (tag too new) or CTOL_ERR_CTAGDATAGONE (too old)."""
    args: tuple[int, str]

class _ReconstInfo(TypedDict):
    """Type-only: one entry of _DataInfo['reconst_info_list']."""
    position_x: float
    position_y: float
    position_theta: float
    position_z: float
    pixel_size_x: float
    pixel_size_y: float
    reconst_image_width: int
    reconst_image_height: int

class _DataInfo(TypedDict):
    """Type-only: returned by CtrlBuffer.collect_data()."""
    data_time: int
    """UNIX time of the first sensor in ns (s * 10**9 + ns)"""
    ctag: int
    """CITIUS tag number (not the SyncDB tag number)"""
    train_id: int
    valid_flag: int
    """1 = valid"""
    success_sensorid_list: list[int]
    """sensors for which the acquisition succeeded"""
    reconst_info_list: list[_ReconstInfo]

@final
class CtrlBuffer:
    """Connection to one CITIUS online data stream."""
    def __init__(self, connect_id: int, /) -> None:
        """Connect to stream ``connect_id`` (see get_connectid_info())."""
    def collect_data(self, images: NDArray[np.float32], sensorid_list: list[int], ctag: int, /) -> _DataInfo:
        """Fill ``images`` (float32, shape (len(sensorid_list), binned_h, binned_w),
        must be an ndarray) with the data of ``ctag``; ``ctag = NEWEST`` takes the
        newest. ``sensorid_list`` must be a list (not tuple)."""
    def read_badpixel_mask(self, masks: NDArray[np.uint8], sensorid_list: list[int], /) -> None:
        """Fill ``masks`` (uint8, shape (len(sensorid_list), binned_h, binned_w))
        with the bad-pixel masks (0 good, 1 bad)."""
    def read_sensoridlist(self) -> list[int]:
        """Sensor IDs of the stream."""
    def read_detidlist(self) -> list[str]:
        """Detector IDs of the stream."""
    def read_binninginfo(self) -> tuple[int, int, int, int]:
        """(binning_x, binning_y, binned_width, binned_height)."""

def get_connectid_info() -> tuple[list[int], list[int]]:
    """(connect_ids, number_of_sensors_per_connect_id)."""
def get_api_version() -> tuple[int, int, int]:
    """(major, minor, patch) of the ctolpy_xfel API."""
