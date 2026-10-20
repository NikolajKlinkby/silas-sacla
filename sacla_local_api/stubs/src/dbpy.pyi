"""SACLA DataAccessUserAPI – SyncDB / run database access, dbpy 1.3.0.

Conventions used by every function:
  bl        beamline number (1, 2, 3)
  run       run number
  taghi     high (upper 32 bit) tag number; ignored in OnlineUserAPI simulation mode
  tag       low (32 bit) tag number
  taglist   **tuple** of int (a list raises TypeError); ascending order unless noted
  equip_id  SyncDB signal / equipment ID, e.g. "xfel_bl_3_tc_bm_1_pd/charge"

All functions raise APIError on failure. Only works where the SACLA databases
are reachable (import is blocked on the HPC login node fep01).
Hand-written stub from DataAccessUserAPI/1.3.0/python/dbpy.cpp.
"""
from typing import Optional, TypedDict

import numpy as np
from numpy.typing import NDArray

_TagList = tuple[int, ...]

class APIError(Exception):
    """Raised on any API failure. ``args == (error_code, error_name)``;
    compare ``args[0]`` with the ``DA_ERR_*`` / ``SY_ERR_*`` constants."""
    args: tuple[int, str]

# --------------------------------------------------------------------- dict types (type-only)
class _RunInfo(TypedDict):
    starttime: float
    stoptime: float
    total_tagnumber: int
    start_tagnumber: int
    end_tagnumber: int
    hightagnumber: int
    comment: str
    fy_id: str
    detectors: str
    runtype: str
    stationnumber: int
    runstatus: int

class _RunSupplementInfo(TypedDict):
    stepnumber: int
    total_stepnumber: int
    start_tagnumber: int
    end_tagnumber: int
    scanmode: str
    comment: str

class _RunConditionInfo(TypedDict):
    value: str
    unit: str
    attribute: str

class _RunDeviceInfo(TypedDict):
    deviceid: int
    madocaobject: str
    devicetype: str
    devicecategory: str
    locationid: int
    location: str
    permissionstaff: int
    permissionuser: int

class _RunDeviceSettingInfo(TypedDict):
    available: int
    enable: int
    userid: str
    devicename: str
    nickname: str
    settings01: str
    settings02: str
    settings03: str
    settings04: str
    settings05: str
    settings06: str
    settings07: str
    settings08: str
    settings09: str
    settings10: str
    settings11: str
    settings12: str
    settings13: str
    settings14: str
    settings15: str
    settings16: str
    settings17: str
    settings18: str
    settings19: str
    settings20: str

class _ConditionMaster(TypedDict):
    conditionid: int
    conditionname: str

class _DeviceCategoryMaster(TypedDict):
    devicecategory_id: int
    devicecategory: str

class _DeviceTypeMaster(TypedDict):
    devicetype_id: int
    devicetype: str

# --------------------------------------------------------------------- runs and tags
def read_runnumber_newest(bl: int, /) -> int:
    """Newest run number of beamline ``bl``."""
def read_runnumber_fromtag(bl: int, taghi: int, tag: int, /) -> int:
    """Run number that contains the given tag."""
def read_runrange(bl: int, jst_t0: int, jst_t1: int, /) -> tuple[int, int]:
    """(begin_run, end_run) for the unix-time (JST) interval [jst_t0, jst_t1]."""
def read_runstatus(bl: int, run: int, /) -> int:
    """-1 = not yet existing, 0 = stopped (ready to read), 1 = paused, 2 = running."""
def read_runinfo(bl: int, run: int, /) -> _RunInfo:
    """Summary of a run (start/stop time, tags, comment, detectors, ...)."""
def read_run_supplementinfo(bl: int, run: int, /) -> Optional[list[_RunSupplementInfo]]:
    """Scan-step information of the run, or None if there is none."""
def read_run_conditioninfo(bl: int, run: int, conditionid: int, /) -> Optional[list[_RunConditionInfo]]:
    """Values of condition ``conditionid`` (see read_conditionmaster()), or None."""
def read_run_deviceinfo(bl: int, run: int, devicecategory_id: int, devicetype_id: int, /) -> Optional[list[_RunDeviceInfo]]:
    """Devices of the given category/type used in the run, or None."""
def read_run_devicesettinginfo(bl: int, run: int, deviceid: int, /) -> Optional[list[_RunDeviceSettingInfo]]:
    """Settings of device ``deviceid`` during the run, or None."""
def read_conditionmaster() -> Optional[list[_ConditionMaster]]:
    """Master list of condition IDs and names, or None."""
def read_devicecategorymaster() -> Optional[list[_DeviceCategoryMaster]]:
    """Master list of device categories, or None."""
def read_devicetypemaster() -> Optional[list[_DeviceTypeMaster]]:
    """Master list of device types, or None."""
def read_comment(bl: int, run: int, /) -> str:
    """Run comment."""
def read_starttime(bl: int, run: int, /) -> float:
    """Run start time (unix epoch, s)."""
def read_stoptime(bl: int, run: int, /) -> float:
    """Run stop time (unix epoch, s)."""
def read_hightagnumber(bl: int, run: int, /) -> int:
    """High tag number of the run."""
def read_start_tagnumber(bl: int, run: int, /) -> tuple[int, int]:
    """(taghi, start_tag) of the run."""
def read_end_tagnumber(bl: int, run: int, /) -> tuple[int, int]:
    """(taghi, end_tag) of the run."""
def read_taglist(bl: int, taghi: int, starttag: int, endtag: int, /) -> tuple[int, ...]:
    """Tags of beamline ``bl`` in [starttag, endtag]."""
def read_taglist_byrun(bl: int, run: int, /) -> tuple[int, ...]:
    """All tags of a run."""
def read_taglist_bydetid(bl: int, run: int, det_id: str, /) -> tuple[int, ...]:
    """Tags of a run for which detector ``det_id`` has data."""
def read_taglist_byequipid(equip_id: str, tag: int, starttag: int, endtag: int, /) -> tuple[int, ...]:
    """Tags in [starttag, endtag] for which ``equip_id`` has data."""
def read_tagnumber_newest(equip_id: str, /) -> tuple[int, int]:
    """(taghi, tag) of the newest tag. taghi is the year (YYYY) in simulation mode."""
def read_hightagnumber_fromtime(time: str, day: int, month: int, year: int, /) -> int:
    """High tag number for a date; ``time`` is "hh:mm:ss"."""
def read_tagnumber_fromtime(time: str, day: int, month: int, year: int, equip_id: str, /) -> int:
    """Tag number for a time; ``time`` is "hh:mm:ss.mmmmmm"."""
def read_timestamp_fromtag(taghi: int, tag: int, equip_id: str, /) -> float:
    """Time stamp (unix epoch, s) of a tag."""

# --------------------------------------------------------------------- detectors
def read_detidlist(bl: int, run: int, /) -> tuple[str, ...]:
    """Detector IDs recorded in a run."""
def read_detidstatus(det_id: str, bl: int, run: int, /) -> int:
    """Status of detector ``det_id`` in the run."""
def read_calibpar_idlist(bl: int, /) -> tuple[str, ...]:
    """IDs of the calibration parameters of beamline ``bl``."""
def read_calibpar(par_id: str, bl: int, taghi: int, tag: int, /) -> NDArray[np.float64]:
    """Calibration parameter ``par_id`` valid at the tag, as a float64 array
    (the C help text says "tuple of string"; the binding returns an ndarray)."""

# --------------------------------------------------------------------- SyncDB data
def read_equiplist() -> tuple[str, ...]:
    """All equipment (signal) IDs."""
def read_syncdatalist(equip_id: str, taghi: int, taglist: _TagList, /) -> tuple[str, ...]:
    """SyncDB values (as strings) of ``equip_id`` for the tags in ``taglist``."""
def read_syncdatalist_float(equip_id: str, taghi: int, taglist: _TagList, /) -> tuple[float, ...]:
    """SyncDB values (as float) of ``equip_id`` for the tags in ``taglist``."""
def read_syncdatalist_current(equip_id: str, taglist: _TagList, /) -> tuple[str, ...]:
    """Online variant: up to 300 recent tags, any order. Values as strings."""
def read_syncdatalist_current_float(equip_id: str, taglist: _TagList, /) -> tuple[float, ...]:
    """Online variant: up to 300 recent tags, any order. Values as float."""
def read_syncdata_current(equip_id: str, tag: int, /) -> tuple[str, ...]:
    """Online variant for a single tag (1-tuple of string)."""
def read_syncdata_current_float(equip_id: str, tag: int, /) -> tuple[float, ...]:
    """Online variant for a single tag (1-tuple of float)."""
def read_syncdatalist_bytagrange(equip_id: str, tag: int, starttag: int, endtag: int, /) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """(tags, values-as-strings) for the tag range [starttag, endtag]."""
def read_syncdatalist_bytagrange_float(equip_id: str, tag: int, starttag: int, endtag: int, /) -> tuple[tuple[int, ...], tuple[float, ...]]:
    """(tags, values-as-float) for the tag range [starttag, endtag]."""

# --------------------------------------------------------------------- beam status
def read_beamstatuslist(bl: int, taghi: int, taglist: _TagList, /) -> tuple[int, ...]:
    """Beam status for each tag in ``taglist``."""
def read_beamstatuslist_current(bl: int, taglist: _TagList, /) -> tuple[int, ...]:
    """Online variant (up to 300 recent tags)."""
def read_beamstatus_current(bl: int, tag: int, /) -> tuple[int, ...]:
    """Online variant for a single tag (1-tuple)."""
def read_beamstatuslist_bytagrange(bl: int, taghi: int, starttag: int, endtag: int, /) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(tags, beam status) for the tag range."""

# --------------------------------------------------------------------- pulse energy (photon_energy in keV)
def read_pulseenergylist_in_joule(equip_id: str, taghi: int, taglist: _TagList, photon_energy: float, /) -> tuple[str, ...]:
    """Pulse energies (J, as strings) of the tags; ``photon_energy`` in keV."""
def read_pulseenergylist_in_joule_float(equip_id: str, taghi: int, taglist: _TagList, photon_energy: float, /) -> tuple[float, ...]:
    """Pulse energies (J, float) of the tags; ``photon_energy`` in keV."""
def read_pulseenergylist_in_joule_current(equip_id: str, taglist: _TagList, photon_energy: float, /) -> tuple[str, ...]:
    """Online variant (up to 300 recent tags), strings."""
def read_pulseenergylist_in_joule_current_float(equip_id: str, taglist: _TagList, photon_energy: float, /) -> tuple[float, ...]:
    """Online variant (up to 300 recent tags), float."""
def read_pulseenergy_in_joule_current(equip_id: str, tag: int, photon_energy: float, /) -> tuple[str, ...]:
    """Online variant for a single tag (1-tuple of string)."""
def read_pulseenergy_in_joule_current_float(equip_id: str, tag: int, photon_energy: float, /) -> tuple[float, ...]:
    """Online variant for a single tag (1-tuple of float)."""
def read_pulseenergylist_in_joule_bytagrange(equip_id: str, tag: int, starttag: int, endtag: int, photon_energy: float, /) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """(tags, pulse energies as strings) for the tag range."""
def read_pulseenergylist_in_joule_bytagrange_float(equip_id: str, tag: int, starttag: int, endtag: int, photon_energy: float, /) -> tuple[tuple[int, ...], tuple[float, ...]]:
    """(tags, pulse energies as float) for the tag range."""

# --------------------------------------------------------------------- accelerator configuration of a run
def read_config_electronenergy(bl: int, run: int, /) -> float:
    """Electron energy (GeV)."""
def read_config_electronenergy_in_GeV(bl: int, run: int, /) -> float:
    """Electron energy (GeV)."""
def read_config_electronenergy_in_MeV(bl: int, run: int, /) -> float:
    """Electron energy (MeV)."""
def read_config_photonenergy(bl: int, run: int, /) -> float:
    """Photon energy (keV)."""
def read_config_photonenergy_in_keV(bl: int, run: int, /) -> float:
    """Photon energy (keV)."""
def read_config_photonenergy_in_eV(bl: int, run: int, /) -> float:
    """Photon energy (eV)."""
def read_config_kpars(bl: int, run: int, /) -> float:
    """Undulator K parameter."""
def read_config_magcurr(bl: int, run: int, /) -> float:
    """Magnet current."""
def read_config_gappos(bl: int, run: int, /) -> float:
    """Undulator gap position."""

def get_api_version() -> tuple[int, int, int]:
    """(major, minor, patch) of the dbpy API."""
