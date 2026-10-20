"""Energy calibration of MPCCD panels, the photon energy and the lower threshold.

An MPCCD pixel does not hold a whole number of photons (a photon's charge can be
shared between pixels), so pixel values are converted to deposited energy:

    keV per ADU of panel p = mp_absgain_p * ev_per_absgain_adu / 1000

mp_absgain (from SACLA's detector info) is the panel's gain in electrons per ADU and
ev_per_absgain_adu is the energy per electron-hole pair in silicon, nominally
3.65 eV. Measured values (MPCCD, 9.9 keV photons) are typically 3.63-3.69 eV.
It is a setting of each MPCCD array (``calibration.ev_per_absgain_adu``) so it can
be tuned at the beamtime.

The photon energy is the config key ``photon_energy_keV`` when it is set (the
run record is then not read at all); when it is null it comes from the run
record (dbpy.read_config_photonenergy_in_keV), read once per run.

One lower threshold per MPCCD array, in keV (``corrections.threshold_keV``,
switched with ``corrections.threshold``). It is converted once to ADU for every
panel, and the panel data are compared with that number as they are: the images
are never rescaled for it.
"""
import math
from typing import Any, Dict, Optional, Sequence

import numpy as np

EV_PER_ABSGAIN_ADU = 3.65      # eV per electron-hole pair in Si (measured: typically 3.63-3.69)
PHOTON_ENERGY_KEY = "photon_energy_keV"

# (beamline, run) -> photon energy (keV) from the run record
_run_energy = {}  # type: Dict[Any, Optional[float]]


def ev_per_absgain_adu(settings: Dict[str, Any]) -> float:
    """The calibration factor of an MPCCD array (settings from mpccd.get_array)."""
    return float((settings.get("calibration") or {}).get("ev_per_absgain_adu", EV_PER_ABSGAIN_ADU))


def kev_per_adu(absgains: Sequence[float], ev_per_adu_gain: float = EV_PER_ABSGAIN_ADU) -> np.ndarray:
    """keV per ADU of every panel (float32 array) from the panels' mp_absgain."""
    g = np.array([float(x) if x and x == x else math.nan for x in absgains], np.float64)
    return (g * float(ev_per_adu_gain) / 1000.0).astype(np.float32)


def absgains(infos: Sequence[Dict[str, Any]]) -> list:
    """mp_absgain of every panel (from read_det_info)."""
    return [float(i.get("mp_absgain", math.nan)) for i in infos]


def threshold_adu(threshold_keV: float, kev_adu: np.ndarray) -> np.ndarray:
    """The keV threshold in ADU of every panel (float32, shape (n_panels, 1, 1)
    so it broadcasts over a stack of panel images)."""
    t = float(threshold_keV) / np.asarray(kev_adu, np.float64)
    return t.astype(np.float32).reshape(-1, 1, 1)


# ---------------------------------------------------------------- photon energy
def photon_energy_override(cfg: Any) -> Optional[float]:
    """The photon energy set in the config (keV), or None."""
    v = cfg.get(PHOTON_ENERGY_KEY, None)
    return float(v) if v is not None else None


def run_photon_energy(bl: int, run: int) -> Optional[float]:
    """Photon energy of a run from the run record (keV; cached per run). None when it
    cannot be read (no SACLA library, smoke mode, unknown run)."""
    key = (int(bl), int(run))
    if key in _run_energy:
        return _run_energy[key]
    value = None  # type: Optional[float]
    from silas.core import sacla

    if sacla.is_live():
        try:
            import dbpy

            value = float(dbpy.read_config_photonenergy_in_keV(int(bl), int(run)))
            if not value > 0:
                value = None
        except Exception as err:  # noqa: BLE001  dbpy missing, APIError, ...
            print("photon energy of run {} not readable: {}".format(run, err))
    _run_energy[key] = value
    return value


def photon_energy(cfg: Any, run: Optional[int] = None) -> Optional[float]:
    """Photon energy in keV: the config value if set (the run record is not read then),
    otherwise the run record of ``run``; None if neither is available."""
    v = photon_energy_override(cfg)
    if v is not None:
        return v
    if run is None:
        return None
    return run_photon_energy(cfg.beamline, run)


def describe(cfg: Any, settings: Dict[str, Any], run: Optional[int] = None,
             infos: Optional[Sequence[Dict[str, Any]]] = None) -> str:
    """Text summary of photon energy, calibration and threshold (for the session)."""
    over = photon_energy_override(cfg)
    e = photon_energy(cfg, run)
    corr = settings.get("corrections", {})
    t = float(corr.get("threshold_keV", 0.0))
    w = ev_per_absgain_adu(settings)
    lines = []
    if over is not None:
        lines.append("photon energy   {:g} keV  (config {})".format(over, PHOTON_ENERGY_KEY))
    else:
        lines.append("photon energy   {}  (config {} = null: from the run record{})".format(
            "{:g} keV".format(e) if e else "?", PHOTON_ENERGY_KEY,
            ", run {}".format(run) if run is not None else ""))
    lines.append("calibration     keV/ADU = mp_absgain x {:g} eV / 1000".format(w))
    lines.append("threshold       {:g} keV  {}{}".format(
        t, "ON" if corr.get("threshold") else "off",
        "  (= {:.2f} photons)".format(t / e) if e else ""))
    if infos:
        k = kev_per_adu(absgains(infos), w)
        ta = threshold_adu(t, k).ravel()
        for i, info in enumerate(infos):
            lines.append("  panel {}  absgain {:.4f}  {:.4f} keV/ADU  threshold {:.1f} ADU{}".format(
                i + 1, float(info.get("mp_absgain", math.nan)), float(k[i]), float(ta[i]),
                "  1 photon = {:.0f} ADU".format(e / float(k[i])) if e else ""))
    return "\n".join(lines)
