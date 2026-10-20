# Regions of interest (ROIs)

A ROI is a rectangle on the MPCCD array, for example around a Bragg peak or a diffuse
feature. The [scan analysis](scans.md) (online and offline) sums the deposited energy in
every ROI for every shot and follows it through a scan. ROIs are set in the session, drawn live in
the [MPCCD viewer](mpccd-viewer.md), and stored in the [configuration](configuration.md),
so every program uses the same ones.

## Setting ROIs

A ROI is defined by two corners read off the viewer, then typed into the session. Step by
step, for a Bragg peak:

1. Start the viewer from the session and find the peak (`a` for the average, mouse wheel to
   zoom in):

    ```python
    start_mpccd_viewer()
    ```

    On the HPC, use stored data: `start_mpccd_viewer(name="octal", run=1234501)` shows the
    average of all shots of the run, and `w` switches between pump on and pump off, which
    shows where the pump changes the signal ([offline view](mpccd-viewer.md#offline-viewer-hpc)).

2. Read the two corners. Put the mouse on the **top-left** corner of the region and note
   the `pixel (row, col) from origin` in the toolbar at the bottom of the window, then do the
   same on the **bottom-right** corner. For example:

    ```
    top-left:      X=   8.950 mm  Y= -14.500 mm   pixel (row 290, col 179) from origin   panel 7 ...
    bottom-right:  X=  11.250 mm  Y= -16.500 mm   pixel (row 330, col 225) from origin   panel 7 ...
    ```

    So the rows go from 290 to 330 and the columns from 179 to 225.

3. Add the ROI in the session, one command per line. `rows` are the two row numbers,
   `cols` the two column numbers (first and last, both included):

    ```python
    add_roi("bragg_002", rows=(290, 330), cols=(179, 225))
    ```

    The ROI table is printed, and the viewer draws the orange rectangle within a second.

4. Check it in the viewer: is the whole peak inside the rectangle? The table at the bottom
   right shows its pixel count, its panels and its mean keV per pixel. If not, adjust and add
   it again under the same name; that replaces it:

    ```python
    add_roi("bragg_002", rows=(285, 335), cols=(175, 230))
    ```

5. Add the next ones the same way, e.g. a background region next to the peak:

    ```python
    add_roi("background", rows=(285, 335), cols=(240, 295))
    ```

6. Look at all of them, or remove one or all:

    ```python
    show_rois()
    remove_roi("background")
    clear_rois()
    ```

ROIs belong to one MPCCD array. Without `name=` the commands use the default array, which
is the one last opened with `start_mpccd_viewer(name=...)` (or set with `use_mpccd("dual")`);
`show_rois()` prints which array it shows and names other arrays that have ROIs. A ROI added
to the wrong array is moved with `remove_roi("peak")` and `add_roi("peak", ..., name="octal")`.

At most **10** ROIs per MPCCD array (the scan viewer switches them on and off with the number
keys). With several MPCCD arrays, all commands take `name="..."` (the array's key or alias); the
default is the first array.

## Coordinates

ROI pixels are counted **from the detector origin** (X = Y = 0, the centre of the
assembled image): rows count downwards, columns to the right, both can be negative. One
pixel is one panel pixel (50 µm), so row 300, column 200 is at X = +10 mm, Y = −15 mm.

Because they are tied to the detector origin and not to a corner of the image, ROIs stay
on the same spot of the detector when the assembled image changes size, e.g. when the
aperture shift (`m` / `M`) or the tuning of a panel changes. (They do not follow a panel
that is moved by tuning: the ROI marks a place on the detector, and whatever panel pixels
are there are used.)

## In the viewer

- ROIs are drawn as orange rectangles with their name; a ROI that lies entirely in a gap or
  outside the panels is drawn grey.
- A table (bottom right) lists for every ROI: the number of panel pixels, the panels it
  covers and its **mean value per pixel** in the shown image (keV, or ADU with `u`; for the
  newest shot or the average, as the image), and at the end which panels have to be read.
- `i` switches the ROIs on and off (`display.rois`).

## How ROIs are used in the analysis

When the ROIs or the geometry change, every ROI is turned once into a list of panel pixels
(`silas.core.roi.RoiMap`):

- for every panel the ROI covers: the indices of the panel pixels inside the rectangle;
- panels covered by no ROI are **not read at all**;
- a panel pixel that appears in two image pixels (the panels are rotated by almost, but not
  exactly, 90°) is counted once; pixels in gaps do not count.

For every shot, only the ROI pixels are corrected: dark subtracted, pixels below the
[threshold](energy-calibration.md#the-threshold) set to 0, and converted to keV with the
panel's calibration. Each ROI gives the deposited energy (keV) and its number of pixels, so
ROIs of different size can be compared as **keV per pixel**. With 3 ROIs on 3 of the 8
panels this takes about 0.03 ms per shot, against about 7–10 ms for correcting all 8
panels (`tests/test_roi.py`).

## Stored in the configuration

```json
"MPCCD-8N0-3-002": {
  "kind": "MPCCD-array",
  "rois": {
    "bragg_002": {"rows": [290, 330], "cols": [180, 225]},
    "diffuse":   {"rows": [-120, -60], "cols": [400, 520]}
  },
  ...
}
```

The order is the order in which they were added (ROI 1 … 10 in the scan viewer). The file
can also be edited by hand; `cfg.reload()` in an open session afterwards.

## Commands

| Command | What it does |
|---|---|
| `add_roi(name, rows, cols)` | add or replace a ROI (pixels from the origin, inclusive) |
| `show_rois()` | print and return the ROIs |
| `remove_roi(name)` | remove one ROI |
| `clear_rois()` | remove all ROIs |
