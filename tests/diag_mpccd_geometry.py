#!/usr/bin/env python3
"""One-off diagnostic: MPCCD octal geometry conventions (runs the olpy simulator + ippy).

    source sacla_local_api/sacla_env.sh
    python tests/diag_mpccd_geometry.py

Read-only. Writes to SILAS/output/diag/:
  mpccd_info.json        detector info of every octal panel (all keys) + image stats
  mpccd_panels.npz       the 8 panel images of one tag (float32) + their info
  ippy_markers.json      where ippy.reconstruction puts known marker pixels
  ippy_variants.png      the octal image assembled with ippy under 4 sign conventions

Questions it answers:
  * units of mp_posx / mp_posy (mm or um)
  * which point of a panel ippy's xPosPixel/yPosPixel refers to, the direction of
    its y axis and of its rotation
"""
import json
import os
import sys
from typing import Any, List

import numpy as np

import ippy
import olpy

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "output", "diag")
PREFIX = "MPCCD-8-2-001-"


def read_panels():
    det_ids = sorted(d for d in olpy.read_detidlist() if d.startswith(PREFIX))
    print("octal panels in the simulator:", ", ".join(det_ids))
    # collect the newest tag on panel 1, then the same tag on all others
    readers = [(d, olpy.StorageReader(d)) for d in det_ids]
    bufs = [olpy.StorageBuffer(r) for _, r in readers]
    tag = readers[0][1].collect(bufs[0], olpy.NEWESTTAG)
    images, infos = [], []
    for (det_id, reader), buf in zip(readers, bufs):
        try:
            got = reader.collect(buf, tag)
        except olpy.APIError as err:
            print("  {}: collect({}) failed {} -> using NEWESTTAG".format(det_id, tag, err.args))
            got = reader.collect(buf, olpy.NEWESTTAG)
        n = buf.read_det_num_index()
        img = np.array(buf.read_det_data(0), dtype=np.float32)
        info = {k: (float(v) if isinstance(v, float) else v) for k, v in buf.read_det_info(0).items()}
        info.update(det_id=det_id, collected_tag=got, num_index=n, shape=list(img.shape),
                    img_min=float(img.min()), img_max=float(img.max()), img_mean=float(img.mean()))
        images.append(img)
        infos.append(info)
    return tag, det_ids, images, infos


def print_table(infos):
    keys = ["det_id", "shape", "num_index", "mp_posx", "mp_posy", "mp_posz", "mp_rotationangle",
            "mp_stagedirection", "mp_shiftweight", "mp_pixelsize_x", "mp_pixelsize_y", "mp_absgain"]
    print("\n" + " | ".join(keys))
    for i in infos:
        print(" | ".join(str(i.get(k, "-")) for k in keys))
    extra = sorted(set().union(*[set(i) for i in infos]) - set(keys))
    print("other keys:", ", ".join(extra))


def marker_test():
    """Synthetic 1024x512 panel with markers; where does ippy put them?"""
    h, w = 1024, 512
    src = np.zeros((h, w), np.float32)
    markers = {  # value: (row, col) in the source panel
        1.0: (0, 0),            # top-left pixel (row 0, col 0)
        2.0: (0, w - 1),        # top-right
        3.0: (h - 1, 0),        # bottom-left
        4.0: (h // 2, w // 2),  # centre
    }
    for v, (r, c) in markers.items():
        src[r, c] = v
    dst_size = 3001  # odd: the centre is pixel 1500
    results = []
    for xpos, ypos, rot in [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (0.0, 100.0, 0.0),
                            (0.0, 0.0, 90.0), (0.0, 0.0, -90.0), (0.0, 0.0, 180.0)]:
        srcs = [{"img": src, "xPosPixel": xpos, "yPosPixel": ypos, "pixelSize": 50,
                 "dScale": 1.0, "iScale": 1.0, "rotAngle": rot, "mask": None}]  # type: List[Any]
        dst = ippy.reconstruction(srcs, dst_size, dst_size, 50, 0)
        found = {}
        for v in markers:
            rr, cc = np.nonzero(np.isclose(dst, v))
            found[str(v)] = [[int(a), int(b)] for a, b in zip(rr, cc)][:4]
        res = {"xPosPixel": xpos, "yPosPixel": ypos, "rotAngle": rot,
               "succeeded": bool(srcs[0].get("succeeded")),
               "dst_centre_rc": [dst_size // 2, dst_size // 2],
               "marker_source_rc": {str(v): list(rc) for v, rc in markers.items()},
               "marker_found_rc": found,
               "nonzero_bbox_rc": _bbox(dst)}
        results.append(res)
        print("ippy x={:6.1f} y={:6.1f} rot={:6.1f}: found {}  bbox {}".format(
            xpos, ypos, rot, found, res["nonzero_bbox_rc"]))
    return results


def _bbox(a):
    rr, cc = np.nonzero(a)
    if not len(rr):
        return None
    return [int(rr.min()), int(rr.max()), int(cc.min()), int(cc.max())]


def assemble_variants(images, infos):
    """Assemble the real panels with ippy under 4 conventions (y sign x rotation sign)."""
    px = float(infos[0].get("mp_pixelsize_x", 50.0))
    pos = np.array([[i["mp_posx"], i["mp_posy"]] for i in infos], float)
    unit_um = 1000.0 if np.abs(pos).max() < 1000 else 1.0  # values look like mm -> um
    print("\npositions look like {} (max |pos| = {:.1f})".format("mm" if unit_um == 1000.0 else "um",
                                                                np.abs(pos).max()))
    g1 = float(infos[0].get("mp_absgain", 1.0)) or 1.0
    size = 2600
    out = {}
    for ysign in (1.0, -1.0):
        for rsign in (1.0, -1.0):
            srcs = []  # type: List[Any]
            for img, i in zip(images, infos):
                srcs.append({"img": img, "xPosPixel": float(i["mp_posx"] * unit_um / px),
                             "yPosPixel": float(ysign * i["mp_posy"] * unit_um / px),
                             "pixelSize": int(round(px)), "dScale": 1.0,
                             "iScale": float(i.get("mp_absgain", 1.0)) / g1,
                             "rotAngle": float(rsign * i["mp_rotationangle"]), "mask": None})
            out[(ysign, rsign)] = ippy.reconstruction(srcs, size, size, int(round(px)), 0)
            print("  y{:+.0f} rot{:+.0f}: succeeded {}".format(ysign, rsign, [s.get("succeeded") for s in srcs]))
    return out, unit_um


def save_png(variants, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 14))
    for ax, ((ysign, rsign), img) in zip(axes.ravel(), sorted(variants.items())):
        finite = img[img != 0]
        vmax = np.percentile(finite, 99.5) if finite.size else 1.0
        ax.imshow(img, vmin=0, vmax=vmax, cmap="viridis", origin="upper")
        ax.set_title("yPos = {:+.0f}*posy, rot = {:+.0f}*angle".format(ysign, rsign))
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    print("saved", path)


def main():
    if not os.environ.get("OL_SIMPATH"):
        sys.exit("OL_SIMPATH not set: source sacla_local_api/sacla_env.sh first")
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    print("olpy", olpy.get_api_version(), "ippy", ippy.get_api_version())

    tag, det_ids, images, infos = read_panels()
    print_table(infos)
    with open(os.path.join(OUT, "mpccd_info.json"), "w") as fh:
        json.dump({"tag": tag, "panels": infos}, fh, indent=2)
    np.savez_compressed(os.path.join(OUT, "mpccd_panels.npz"), images=np.stack(images),
                        det_ids=np.array(det_ids), info_json=json.dumps(infos))

    print("\n--- ippy marker test")
    markers = marker_test()
    with open(os.path.join(OUT, "ippy_markers.json"), "w") as fh:
        json.dump(markers, fh, indent=2)

    print("\n--- ippy assembly of the real panels")
    variants, unit_um = assemble_variants(images, infos)
    save_png(variants, os.path.join(OUT, "ippy_variants.png"))
    print("\ndone: files in", OUT)


if __name__ == "__main__":
    main()
