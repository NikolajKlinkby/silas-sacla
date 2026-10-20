"""MPCCD multi-panel geometry: put the panels where they are in real space.

Shared by online (olpy) and offline (stpy): the geometry comes from the detector
info dictionary that both return for every panel (``read_det_info(0)``).

Conventions (from the SACLA document "MPCCD octal image assembly algorithm" and
verified on the octal MPCCD of the olpy simulator, see tests/diag_mpccd_geometry.py):

  * ``mp_posx``, ``mp_posy``: position of the panel origin in the detector plane,
    in micrometres. The origin of a panel is the CENTRE OF ITS TOP-LEFT PIXEL
    (row 0, column 0 of the panel image).
  * the detector frame has X to the right and Y UP; image rows go DOWN, so
    image row = -Y / pixel size.
  * ``mp_rotationangle``: rotation of the panel about its origin, degrees,
    counterclockwise as seen in the assembled image.
  * ``mp_pixelsize_x/_y`` (olpy) or ``mp_pixelsizex/y`` (stpy): micrometres per pixel.
  * gain: panels are normalised to panel 1, data * (mp_absgain / mp_absgain of panel 1).
  * aperture: positions are for the closed aperture. Opening it shifts each panel
    by w*L along the stage direction (``mp_stagedirection``, ``mp_shiftweight``,
    L = manipulator shift in micrometres; stpy gives ``mp_manipulator_shift``,
    olpy does not, so online L must be given; default 0 = closed).

The assembled image is centred on the detector origin (X = Y = 0 is the centre
pixel), has the panel pixel size, and pixels between panels are NaN.

Assembly uses a pixel map computed once, so one image costs a single numpy
indexing operation (milliseconds), fast enough for live display. The map is an
inverse nearest-neighbour map (for every image pixel: the nearest panel pixel),
like ippy.reconstruction with method 0: no holes, no pixel filled twice.

Small corrections can be applied on top of the SACLA geometry (tuned live in the
viewer): a manipulator shift and per-panel offsets (dx_um, dy_um, drot_deg).
"""
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def _first(info: Dict[str, Any], *keys: str, default: Optional[float] = None) -> float:
    for k in keys:
        if k in info:
            return float(info[k])
    if default is None:
        raise KeyError("none of {} in detector info".format(keys))
    return default


class Panel(object):
    """Geometry of one MPCCD panel (sensor)."""

    def __init__(self, det_id: str, shape: Tuple[int, int], posx_um: float, posy_um: float,
                 rotation_deg: float, pixel_um: float, gain: float = 1.0,
                 stage_direction_deg: float = 0.0, shift_weight: float = 0.0) -> None:
        self.det_id = det_id
        self.shape = (int(shape[0]), int(shape[1]))  # (rows, columns) of the panel image
        self.posx_um = float(posx_um)
        self.posy_um = float(posy_um)
        self.rotation_deg = float(rotation_deg)
        self.pixel_um = float(pixel_um)
        self.gain = float(gain)
        self.stage_direction_deg = float(stage_direction_deg)
        self.shift_weight = float(shift_weight)

    @classmethod
    def from_info(cls, det_id: str, info: Dict[str, Any], shape: Optional[Tuple[int, int]] = None) -> "Panel":
        """From a read_det_info() dictionary (olpy or stpy key names)."""
        if shape is None:
            shape = (int(info["ysize"]), int(info["xsize"]))
        return cls(det_id=det_id, shape=shape,
                   posx_um=_first(info, "mp_posx"), posy_um=_first(info, "mp_posy"),
                   rotation_deg=_first(info, "mp_rotationangle"),
                   pixel_um=_first(info, "mp_pixelsize_x", "mp_pixelsizex", default=50.0),
                   gain=_first(info, "mp_absgain", default=1.0),
                   stage_direction_deg=_first(info, "mp_stagedirection", default=0.0),
                   shift_weight=_first(info, "mp_shiftweight", default=0.0))

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__, shape=list(self.shape))

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Panel":
        return cls(**dict(d, shape=tuple(d["shape"])))

    def origin_um(self, manipulator_shift_um: float = 0.0) -> Tuple[float, float]:
        """(X, Y) of the panel origin including the aperture shift."""
        a = math.radians(self.stage_direction_deg)
        s = self.shift_weight * manipulator_shift_um
        return self.posx_um + math.cos(a) * s, self.posy_um + math.sin(a) * s

    def pixel_positions(self, manipulator_shift_um: float = 0.0,
                        corners_only: bool = False) -> Tuple[np.ndarray, np.ndarray]:
        """(X, Y) in micrometres of every pixel centre, arrays of the panel shape
        (corners_only: 2 x 2 arrays of the corner pixels)."""
        if corners_only:
            h, w = self.shape[0] - 1, self.shape[1] - 1
            rows, cols = np.array([[0.0, 0.0], [h, h]]), np.array([[0.0, w], [0.0, w]])
        else:
            rows, cols = np.mgrid[0:self.shape[0], 0:self.shape[1]].astype(np.float64)
        th = math.radians(self.rotation_deg)
        # panel frame in image orientation: columns -> +X, rows -> -Y; then rotate CCW by th
        u, v = cols, -rows
        x0, y0 = self.origin_um(manipulator_shift_um)
        x = x0 + self.pixel_um * (u * math.cos(th) - v * math.sin(th))
        y = y0 + self.pixel_um * (u * math.sin(th) + v * math.cos(th))
        return x, y

    def corner_positions(self, manipulator_shift_um: float = 0.0) -> Tuple[List[float], List[float]]:
        """X and Y (um) of the 4 corner pixel centres."""
        x, y = self.pixel_positions(manipulator_shift_um, corners_only=True)
        return [float(v) for v in x.ravel()], [float(v) for v in y.ravel()]

    def to_panel_pixel(self, X: np.ndarray, Y: np.ndarray,
                       manipulator_shift_um: float = 0.0) -> Tuple[np.ndarray, np.ndarray]:
        """(row, column) in this panel (float) of detector positions X, Y (um)."""
        th = math.radians(self.rotation_deg)
        x0, y0 = self.origin_um(manipulator_shift_um)
        dx, dy = (X - x0) / self.pixel_um, (Y - y0) / self.pixel_um
        u = dx * math.cos(th) + dy * math.sin(th)
        v = -dx * math.sin(th) + dy * math.cos(th)
        return -v, u

    def __repr__(self) -> str:
        return "Panel({}, pos=({:.0f}, {:.0f}) um, rot={:.2f} deg)".format(
            self.det_id, self.posx_um, self.posy_um, self.rotation_deg)


class Geometry(object):
    """Pixel map for a set of panels (e.g. the 8 panels of an octal MPCCD)."""

    def __init__(self, panels: Sequence[Panel], manipulator_shift_um: float = 0.0,
                 margin: int = 2, rounding: str = "floor",
                 sub_pixel: Tuple[float, float] = (0.0, 0.0)) -> None:
        """rounding ("floor", the default, matches ippy.reconstruction best so far;
        "trunc": towards zero like a C cast; "rint": nearest) and sub_pixel (row, column offset in panel
        pixels added before rounding) only decide WHICH neighbouring panel pixel
        an image pixel takes; they do not move the panels."""
        if not panels:
            raise ValueError("no panels")
        if rounding not in ("trunc", "floor", "rint"):
            raise ValueError("rounding must be 'trunc', 'floor' or 'rint'")
        self.panels = list(panels)
        self.manipulator_shift_um = float(manipulator_shift_um)
        self.rounding = rounding
        self.sub_pixel = (float(sub_pixel[0]), float(sub_pixel[1]))
        self._owner = None  # type: Optional[np.ndarray]
        self._owner_src = None  # type: Optional[np.ndarray]
        self.pixel_um = self.panels[0].pixel_um
        g1 = self.panels[0].gain or 1.0
        self.gain_factors = np.array([p.gain / g1 for p in self.panels], np.float32)
        """Relative gain of each panel (panel 1 = 1)."""

        # extent of the image from the panel corners
        xs, ys = [], []
        for p in self.panels:
            x, y = p.corner_positions(self.manipulator_shift_um)
            xs.extend(x)
            ys.extend(y)
        half_r = int(math.ceil(max(abs(v) for v in ys) / self.pixel_um)) + margin
        half_c = int(math.ceil(max(abs(v) for v in xs) / self.pixel_um)) + margin
        self.shape = (2 * half_r + 1, 2 * half_c + 1)
        """(rows, columns) of the assembled image (always odd: centre pixel = origin)."""
        self.centre = (half_r, half_c)
        """(row, column) of the detector origin X = Y = 0 in the assembled image."""

        # inverse map: for every image pixel inside a panel, the nearest panel pixel
        self._dst = []  # type: List[np.ndarray]
        self._src = []  # type: List[np.ndarray]
        for p in self.panels:
            x, y = p.corner_positions(self.manipulator_shift_um)
            c_lo = max(int(math.floor(min(x) / self.pixel_um)) + half_c - 1, 0)
            c_hi = min(int(math.ceil(max(x) / self.pixel_um)) + half_c + 1, self.shape[1] - 1)
            r_lo = max(int(math.floor(-max(y) / self.pixel_um)) + half_r - 1, 0)
            r_hi = min(int(math.ceil(-min(y) / self.pixel_um)) + half_r + 1, self.shape[0] - 1)
            rr, cc = np.mgrid[r_lo:r_hi + 1, c_lo:c_hi + 1]
            X = (cc - half_c) * self.pixel_um
            Y = -(rr - half_r) * self.pixel_um
            row_s, col_s = p.to_panel_pixel(X, Y, self.manipulator_shift_um)
            rnd = {"trunc": np.trunc, "floor": np.floor, "rint": np.rint}[rounding]  # type: Any
            row_i = rnd(row_s + self.sub_pixel[0]).astype(np.int64)
            col_i = rnd(col_s + self.sub_pixel[1]).astype(np.int64)
            ok = (row_i >= 0) & (row_i < p.shape[0]) & (col_i >= 0) & (col_i < p.shape[1])
            self._dst.append((rr[ok] * self.shape[1] + cc[ok]).astype(np.int64))
            self._src.append((row_i[ok] * p.shape[1] + col_i[ok]).astype(np.int64))

    # ------------------------------------------------------------ construction
    @classmethod
    def from_infos(cls, det_ids: Sequence[str], infos: Sequence[Dict[str, Any]],
                   shapes: Optional[Sequence[Tuple[int, int]]] = None,
                   manipulator_shift_um: Optional[float] = None, **kwargs: Any) -> "Geometry":
        """From read_det_info(0) of each panel. The manipulator shift is taken from
        the info (stpy) unless given (olpy has none -> 0)."""
        panels = [Panel.from_info(d, i, shapes[k] if shapes else None)
                  for k, (d, i) in enumerate(zip(det_ids, infos))]
        if manipulator_shift_um is None:
            manipulator_shift_um = float(infos[0].get("mp_manipulator_shift", 0.0))
        return cls(panels, manipulator_shift_um, **kwargs)

    def tweaked(self, manipulator_shift_um: Optional[float] = None,
                panel_offsets: Optional[Dict[str, Any]] = None) -> "Geometry":
        """A new Geometry with corrections on top of this one. ``panel_offsets``:
        {"1": {"dx_um": .., "dy_um": .., "drot_deg": ..}, ...} with 1-based panel numbers."""
        panels = []
        for k, p in enumerate(self.panels):
            off = (panel_offsets or {}).get(str(k + 1)) or {}
            q = Panel.from_dict(p.to_dict())
            q.posx_um += float(off.get("dx_um", 0.0))
            q.posy_um += float(off.get("dy_um", 0.0))
            q.rotation_deg += float(off.get("drot_deg", 0.0))
            panels.append(q)
        shift = self.manipulator_shift_um if manipulator_shift_um is None else manipulator_shift_um
        return Geometry(panels, shift, rounding=self.rounding, sub_pixel=self.sub_pixel)

    def to_json(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump({"manipulator_shift_um": self.manipulator_shift_um,
                       "panels": [p.to_dict() for p in self.panels]}, fh, indent=2)

    @classmethod
    def from_json(cls, path: str) -> "Geometry":
        with open(path) as fh:
            d = json.load(fh)
        return cls([Panel.from_dict(p) for p in d["panels"]], d.get("manipulator_shift_um", 0.0))

    # ------------------------------------------------------------ use
    def assemble(self, panels: Sequence[np.ndarray], gain: bool = True, fill: float = np.nan,
                 out: Optional[np.ndarray] = None, scale: Any = None) -> np.ndarray:
        """Assembled float32 image from the panel images (same order as the panels).
        scale: a factor per panel (e.g. keV per ADU from silas.core.energy), used instead
        of gain; otherwise gain=True normalises every panel to the gain of panel 1."""
        if len(panels) != len(self.panels):
            raise ValueError("{} images for {} panels".format(len(panels), len(self.panels)))
        if out is None:
            out = np.empty(self.shape, np.float32)
        out.fill(fill)
        flat = out.reshape(-1)
        for k, img in enumerate(panels):
            data = np.asarray(img, np.float32).ravel()[self._src[k]]
            if scale is not None:
                flat[self._dst[k]] = data * np.float32(scale[k])
            else:
                flat[self._dst[k]] = data * self.gain_factors[k] if gain else data
        return out

    def lookup(self, row: int, col: int) -> Optional[Tuple[int, int, int]]:
        """(panel index, panel row, panel column) shown at image pixel (row, col),
        None in a gap or outside the image."""
        if not (0 <= row < self.shape[0] and 0 <= col < self.shape[1]):
            return None
        owner, src = self._owner, self._owner_src
        if owner is None or src is None:
            n = self.shape[0] * self.shape[1]
            owner = np.full(n, -1, np.int16)
            src = np.full(n, -1, np.int64)
            for k, (d, si) in enumerate(zip(self._dst, self._src)):
                owner[d] = k
                src[d] = si
            self._owner, self._owner_src = owner, src
        i = row * self.shape[1] + col
        k = int(owner[i])
        if k < 0:
            return None
        flat = int(src[i])
        w = self.panels[k].shape[1]
        return k, flat // w, flat % w

    def pixel_of(self, x_mm: float, y_mm: float) -> Tuple[int, int]:
        """Image (row, column) at detector position (X, Y) in mm."""
        p = self.pixel_um / 1000.0
        return int(round(-y_mm / p)) + self.centre[0], int(round(x_mm / p)) + self.centre[1]

    def mask(self) -> np.ndarray:
        """Boolean image: True where a panel pixel lands."""
        m = np.zeros(self.shape[0] * self.shape[1], bool)
        for idx in self._dst:
            m[idx] = True
        return m.reshape(self.shape)

    def extent_mm(self) -> Tuple[float, float, float, float]:
        """(left, right, bottom, top) in mm for matplotlib's imshow(extent=...), so
        the axes show real detector coordinates (X right, Y up)."""
        p = self.pixel_um / 1000.0
        r0, c0 = self.centre
        return (-(c0 + 0.5) * p, (self.shape[1] - c0 - 0.5) * p,
                -(self.shape[0] - r0 - 0.5) * p, (r0 + 0.5) * p)

    def outlines_mm(self) -> List[np.ndarray]:
        """Corner polygon (5 x 2 array of X, Y in mm) of every panel, for drawing."""
        out = []
        for p in self.panels:
            x, y = p.pixel_positions(self.manipulator_shift_um)
            corners = [(0, 0), (0, -1), (-1, -1), (-1, 0), (0, 0)]
            out.append(np.array([[x[r, c] / 1000.0, y[r, c] / 1000.0] for r, c in corners]))
        return out

    def ippy_sources(self, panels: Sequence[np.ndarray], gain: bool = True) -> List[Any]:
        """The same geometry as input for ippy.reconstruction (cross-check):
        ippy.reconstruction(srcs, shape[1], shape[0], int(pixel_um), 0).
        (List[Any]: ippy's stub wants its own TypedDict; every required key is set here.)"""
        srcs = []  # type: List[Any]
        for k, (p, img) in enumerate(zip(self.panels, panels)):
            x0, y0 = p.origin_um(self.manipulator_shift_um)
            srcs.append({"img": np.asarray(img, np.float32), "xPosPixel": float(x0 / self.pixel_um),
                         "yPosPixel": float(-y0 / self.pixel_um), "pixelSize": int(round(p.pixel_um)),
                         "dScale": 1.0, "iScale": float(self.gain_factors[k]) if gain else 1.0,
                         "rotAngle": float(p.rotation_deg), "mask": None})
        return srcs

    def __repr__(self) -> str:
        return "Geometry({} panels, image {}x{}, centre {})".format(
            len(self.panels), self.shape[0], self.shape[1], self.centre)
