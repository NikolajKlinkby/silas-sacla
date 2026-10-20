"""SACLA ImageProcessingUserAPI, ippy 2.1.1 – image processing on NumPy arrays.

Pure computation, works anywhere (also locally).
Images must be 2-D and of dtype float32, uint16 or uint8 (anything else raises
TypeError); masks must be 2-D integer arrays (int32/int64/uint8/uint16/uint32/uint64)
of the same shape, where mask > 0 selects the pixels used.
Hand-written stub from ImageProcessingUserAPI/2.1.1/python/ippy.cpp.
"""
from typing import Optional, TypedDict, TypeVar, Union

import numpy as np
from numpy.typing import NDArray
from typing_extensions import NotRequired

_ImgT = TypeVar("_ImgT", np.float32, np.uint16, np.uint8)
_Image = Union[NDArray[np.float32], NDArray[np.uint16], NDArray[np.uint8]]
_Mask = Union[
    NDArray[np.int32], NDArray[np.int64], NDArray[np.uint8],
    NDArray[np.uint16], NDArray[np.uint32], NDArray[np.uint64],
]

class APIError(Exception):
    """Raised on any API failure. ``args == (error_code, error_name)``;
    compare ``args[0]`` with the ``IP_ERR_*`` constants."""
    args: tuple[int, str]

class _ReconstructionSource(TypedDict):
    """Type-only: one entry of ``srcs`` for reconstruction(). ALL keys except
    'succeeded' are required (a missing key is not checked by the C code and
    can crash the interpreter). Float values must be real Python floats
    (``1`` instead of ``1.0`` raises TypeError)."""
    img: _Image
    xPosPixel: float
    """x position (pixel) relative to the centre of the destination image"""
    yPosPixel: float
    """y position (pixel) relative to the centre of the destination image"""
    pixelSize: int
    """pixel size of this image (micrometre per pixel)"""
    dScale: float
    """distance scale (destination / source)"""
    iScale: float
    """intensity scale (destination / source)"""
    rotAngle: float
    """rotation angle, counterclockwise positive (degrees)"""
    mask: Optional[_Mask]
    succeeded: NotRequired[bool]
    """written by reconstruction(): True if this source was placed"""

def roi(src: NDArray[_ImgT], startX: int, startY: int, roiW: int, roiH: int, /) -> NDArray[_ImgT]:
    """Cut out the region [startY:startY+roiH, startX:startX+roiW]; keeps the dtype."""
def binning(src: _Image, binX: int, binY: int, /) -> NDArray[np.float32]:
    """Bin by binX x binY pixels."""
def scale(src: _Image, dstW: int, dstH: int, method: int = 0, /) -> NDArray[np.float32]:
    """Resize to dstW x dstH. method: 0 nearest neighbour, 1 bicubic."""
def flip(src: _Image, aroundYAxis: bool = True, /) -> NDArray[np.float32]:
    """Mirror around the y axis (True, default) or the x axis (False)."""
def rotate_multi90(src: _Image, multiplier: int, /) -> NDArray[np.float32]:
    """Rotate by multiplier x 90 degrees (1, 2 or 3)."""
def threshold(src: _Image, threshold: float = 0.0, fillValue: float = 0.0, /) -> NDArray[np.float32]:
    """Replace pixels below ``threshold`` by ``fillValue``."""
def linear_arithmetic(srcX: _Image, srcY: _Image, a: float = 1.0, b: float = -1.0, /) -> NDArray[np.float32]:
    """a*srcX + b*srcY (default: srcX - srcY). Same shape and dtype required."""
def projection(src: _Image, start: int, end: int, isYDirection: bool = True, /) -> NDArray[np.float32]:
    """1-D projection over rows/columns start..end (zero based);
    isYDirection=True projects along y."""
def min_max_avg(src: _Image, mask: Optional[_Mask] = None, /) -> tuple[float, float, float]:
    """(min, max, mean) over the masked area (min/max are int for integer images)."""
def histogram(src: _Image, binWidth: float, min: float, max: float, mask: Optional[_Mask] = None, /) -> NDArray[np.int32]:
    """1-D histogram (int32 counts) of the masked area over [min, max]."""
def intensity_weighted_centroid(src: _Image, mask: Optional[_Mask] = None, /) -> tuple[float, float]:
    """(x, y) of the intensity-weighted centroid."""
def divided_ring_roi_average(
    img: _Image, centerX: float, centerY: float, disInterRing: float, thickRing: float,
    thetaStart: float, theta: float, mask: Optional[_Mask] = None, /,
) -> float:
    """Mean of a ring sector: inner radius disInterRing, width thickRing,
    from angle thetaStart over theta degrees (counterclockwise, 0 = +x)."""
def radial_profile(
    img: _Image, centerX: float, centerY: float, disStart: float, disEnd: float,
    thickRingStep: float, thetaStart: float = 0.0, theta: float = 360.0,
    mask: Optional[_Mask] = None, /,
) -> NDArray[np.float32]:
    """Radial profile from disStart to disEnd in steps of thickRingStep, over the
    sector [thetaStart, thetaStart+theta] degrees. Returns a 2-D float32 array."""
def reconstruction(
    srcs: list[_ReconstructionSource], dstW: int, dstH: int, dstPixelSize: int, method: int = 0, /,
) -> NDArray[np.float32]:
    """Assemble several panels (e.g. MPCCD octal) into one image of dstW x dstH
    pixels of dstPixelSize micrometre. ``srcs`` must be a list (not tuple).
    method: 0 nearest neighbour, 1 bicubic, 2 bilinear. Sets srcs[i]['succeeded']."""
def reconstruction_inplace_without_init(
    dst: NDArray[np.float32], srcs: list[_ReconstructionSource], dstW: int, dstH: int,
    dstPixelSize: int, method: int = 0, /,
) -> None:
    """Like reconstruction() but writes into the caller-allocated ``dst`` without
    clearing it first (to accumulate several events)."""

def get_api_version() -> tuple[int, int, int]:
    """(major, minor, patch) of the ippy API."""
