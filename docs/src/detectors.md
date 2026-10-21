# Detectors and aliases

SACLA has two kinds of data sources (see [SACLA basics](sacla-basics.md#two-kinds-of-data)):

- **detectors**: image detectors such as MPCCDs and cameras, named like `MPCCD-2N0-M02-001`;
- **point detectors**: one value per pulse (photodiodes, motors, status flags), named like
  `xfel_bl_3_tc_bm_1_pd/charge`.

The configuration keeps a list of each, so the analysis knows what to read.

## The standard set

`setup_config(beamline=3)` ([Configuration](configuration.md#setting-it-up)) starts the config
with the common BL3 detectors (`silas/core/standard.py`); change or add to them for your setup:

| Alias | True name | Meaning |
|---|---|---|
| `optical_shutter` | `xfel_bl_3_lh1_shutter_1_open_valid/status` | optical (pump) laser shutter open |
| `xfel_shutter` | `xfel_bl_3_shutter_1_open_valid/status` | X-ray shutter open |
| `i0_1`, `i0_2` | `xfel_bl_3_st_2_pd_user_6/7_fitting_peak/voltage` | incident intensity photodiodes |
| `delay_m` | `xfel_bl_3_st_2_motor_1/position` | pump-probe delay stage (pulse) |
| `delay_tt` | `xfel_bl_3_st_1_motor_73/position` | delay of the timing tool |
| `octal` | `MPCCD-8N0-3-002` (panels `-1` … `-8`) | octal MPCCD, all 8 panels (the default array) |
| `dual` | `MPCCD-2B1-M03-001` (panels `-1`, `-2`) | dual MPCCD (30 Hz: tag step 2, threshold 2 keV) |

Other motors of an experiment (e.g. fluence or a second delay) are added with
`add_point_detector` (below). `add_standard_detectors()` adds whatever of the standard set is
missing to an existing config; entries and aliases already there are kept.

## Finding names

At SACLA (live mode):

```python
run_detectors()                        # image detectors in the newest run
run_detectors(1561460)                 # ... in a given run
find_equipment("*bl_3*motor*")         # point detectors matching a pattern
find_equipment("*bl_3*bm_1*")
```

## Adding them

```python
add_detector("MPCCD-2N0-M02-001", alias="mpccd", kind="MPCCD")

add_point_detector("xfel_bl_3_tc_bm_1_pd/charge", alias="i0")
add_point_detector("xfel_bl_3_st_5_motor_user_1/position", alias="time_delay", unit="pulse")

add_point_detectors([                  # several at once, no aliases
    "xfel_bl_3_shutter_1_open_valid/status",
    "xfel_mon_bpm_bl3_0_3_beamstatus/summary",
])

show_detectors()
```

```
detectors (1):
  mpccd                  MPCCD-2N0-M02-001  (MPCCD)
point_detectors (4):
  i0                     xfel_bl_3_tc_bm_1_pd/charge
  time_delay             xfel_bl_3_st_5_motor_user_1/position  [pulse]
  -                      xfel_bl_3_shutter_1_open_valid/status
  -                      xfel_mon_bpm_bl3_0_3_beamstatus/summary
```

Calling `add_...` again for the same name updates its alias, kind or unit.

## Aliases

An alias is a short, meaningful name for a detector or point detector, for example
`time_delay` for the motor that moves the delay stage. Aliases are optional.

- **Both names always work.** Every command that takes a name accepts the alias or the true
  name: `read_point("time_delay")` and `read_point("xfel_bl_3_st_5_motor_user_1/position")`
  are the same. `resolve("time_delay")` shows the true name.
- **Without an alias the true name is used**, so nothing breaks if no alias is set.
- **Aliases are unique**: an alias cannot be used twice, and it cannot look like a true
  equipment name (contain `/` or start with `xfel_`).
- **Stored data uses true names.** Files written by SILAS always contain the true
  names; aliases are applied when data is shown. An alias added later therefore also applies
  to data recorded before it was set.

```python
set_alias("xfel_bl_3_tc_bm_2_pd/charge", "i0_bm2")   # add or change
set_alias("i0_bm2", None)                            # remove the alias
remove_detector("i0")                                # remove the entry completely
```

## MPCCD arrays

A detector made of several sensors, like the octal MPCCD, is one entry in `detectors`
with kind `MPCCD-array` that lists its panel IDs:

```python
add_mpccd("MPCCD-8-2-001")      # panels MPCCD-8-2-001-1 ... -8, alias "octal"
```

See the [MPCCD viewer](mpccd-viewer.md) for its settings.

## Units

Point detectors can have a unit, shown next to the name in tables and plots
(`time_delay [pulse]`):

```python
set_unit("time_delay", "pulse")
set_unit("i0", None)            # remove
```

The unit is a label only; values are not converted.
