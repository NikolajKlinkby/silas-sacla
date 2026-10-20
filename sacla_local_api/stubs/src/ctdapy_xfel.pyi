"""SACLA CITIUSDataAccessUserAPI, ctdapy_xfel 1.5.0 – offline CITIUS data access.

Typical use::

    if ctdapy_xfel.get_runstatus(bl, run) == ctdapy_xfel.CTDA_RUN_STATUS_CAN_READ:
        buf = ctdapy_xfel.CtrlBuffer(bl, run)
        sid = buf.read_sensoridlist()[0]
        img = np.empty((ctdapy_xfel.CITIUS_IMAGE_HEIGHT, ctdapy_xfel.CITIUS_IMAGE_WIDTH), np.float32)
        buf.read_image(img, sid, buf.read_taglist()[0])

Images and masks are written into caller-allocated arrays (buffer protocol).
Hand-written stub from CITIUSDataAccessUserAPI/1.5.0/python/ctdapy_xfel.cpp.
"""
from typing import TypedDict, final

import numpy as np
from numpy.typing import NDArray

class APIError(Exception):
    """Raised on any API failure. ``args == (error_code, error_name)``;
    compare ``args[0]`` with the ``CTDA_ERR_*`` constants."""
    args: tuple[int, str]

class _ReconstInfo(TypedDict):
    """Type-only: returned by CtrlBuffer.read_reconstinfo()."""
    position_x: float
    """x position relative to the centre of the reconstructed image (micrometre)"""
    position_y: float
    """y position relative to the centre of the reconstructed image (micrometre)"""
    position_theta: float
    """rotation angle, counterclockwise positive (degrees)"""
    position_z: float
    """z position relative to the most downstream sensors (micrometre)"""
    pixel_size_x: float
    """x pixel size of the reconstructed image (micrometre per pixel)"""
    pixel_size_y: float
    """y pixel size of the reconstructed image (micrometre per pixel)"""

@final
class CtrlBuffer:
    """Access to the CITIUS data of one run."""
    def __init__(self, bl: int, run: int, fy_id: str = ..., /) -> None:
        """Open run ``run`` of beamline ``bl``; ``fy_id`` optionally selects the
        fiscal-year data area."""
    def read_sensoridlist(self) -> list[int]:
        """Sensor IDs of the run."""
    def read_detidlist(self) -> list[str]:
        """Detector IDs of the run."""
    def read_taglist(self) -> list[int]:
        """Tag numbers of the run."""
    def read_image(self, image: NDArray[np.float32], sensorid: int, tag: int, /) -> None:
        """Write the image of ``sensorid`` at ``tag`` into ``image``
        (float32, shape (CITIUS_IMAGE_HEIGHT, CITIUS_IMAGE_WIDTH))."""
    def read_badpixel_mask(self, mask: NDArray[np.uint8], sensorid: int, /) -> None:
        """Write the bad-pixel mask (0 good, 1 bad) of ``sensorid`` into ``mask``
        (uint8, shape (CITIUS_IMAGE_HEIGHT, CITIUS_IMAGE_WIDTH))."""
    def read_reconstinfo(self, sensorid: int, tag: int, /) -> _ReconstInfo:
        """Geometry of ``sensorid`` at ``tag`` for image reconstruction."""

def get_runstatus(bl: int, run: int, /) -> int:
    """Run status, one of the CTDA_RUN_STATUS_* constants."""
def get_api_version() -> tuple[int, int, int]:
    """(major, minor, patch) of the ctdapy_xfel API."""
