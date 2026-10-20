#!/usr/bin/env python3
"""One-time move from the old flat layout to the new folder structure.

    python3 src/migrate.py          show what would happen (nothing is changed)
    python3 src/migrate.py --yes    do it

Moves downloaded files into resources/ (so nothing has to be downloaded again):
    Offline/  Online/   -> resources/manuals/Offline/  resources/manuals/Online/
    Examples/NNNN/      -> resources/examples/NNNN/
    web/                -> resources/web/
and puts the old generated pages and old scripts into _old/ (delete it when happy):
    SACLA_*.md  System/  Overview/  __pycache__/  build_examples.py  build_overview.py  build_system.py
    build_software.py  plus leftovers such as *.crdownload and stray readme copies
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "resources"
OLD = ROOT / "_old"

MOVES = [
    (ROOT / "Offline", RES / "manuals" / "Offline"),
    (ROOT / "Online", RES / "manuals" / "Online"),
    (ROOT / "web", RES / "web"),
]
OLD_SCRIPTS = ["build_examples.py", "build_overview.py", "build_system.py", "build_software.py"]


def plan():
    actions = []   # (src, dst)

    def merge(src, dst):
        """Move src to dst; if dst exists, move the contents one by one."""
        if not src.exists():
            return
        if not dst.exists():
            actions.append((src, dst))
            return
        if src.is_dir():
            for child in sorted(src.iterdir()):
                merge(child, dst / child.name)
        else:
            actions.append((src, OLD / "duplicates" / src.relative_to(ROOT)))

    for src, dst in MOVES:
        merge(src, dst)
    ex = ROOT / "Examples"
    if ex.is_dir():
        for child in sorted(ex.iterdir()):
            if child.is_dir() and child.name.isdigit():
                merge(child, RES / "examples" / child.name)
            else:
                actions.append((child, OLD / "Examples" / child.name))
    for f in sorted(ROOT.glob("SACLA_*.md")):
        actions.append((f, OLD / f.name))
    for d in ("System", "Overview", "__pycache__"):
        if (ROOT / d).exists():
            actions.append((ROOT / d, OLD / d))
    for name in OLD_SCRIPTS:
        if (ROOT / name).exists():
            actions.append((ROOT / name, OLD / name))
    return actions


def main():
    do_it = "--yes" in sys.argv
    actions = plan()
    if not actions:
        print("Nothing to migrate – the folder already uses the new layout.")
        return
    leftovers = []
    for src, dst in actions:
        print(f"{'move' if do_it else 'would move'}  {src.relative_to(ROOT)}  ->  {dst.relative_to(ROOT)}")
        if do_it:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
    if do_it:
        for d in ("Offline", "Online", "Examples", "web"):
            p = ROOT / d
            if p.is_dir():
                try:
                    p.rmdir()                       # only if empty
                except OSError:
                    leftovers.append(d)
        # leftover partial downloads inside the moved manual folders
        for f in (RES / "manuals").glob("*/*.crdownload"):
            dst = OLD / "crdownload" / f.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dst))
            print(f"move  {f.relative_to(ROOT)}  ->  {dst.relative_to(ROOT)}")
        print("\nDone. Now run:  python3 build_all.py")
        if leftovers:
            print("Not empty, please check:", ", ".join(leftovers))
        print("Old pages and scripts are in _old/ – delete that folder when you are happy.")
    else:
        print(f"\n{len(actions)} moves planned. Nothing changed yet – run with --yes to do it.")


if __name__ == "__main__":
    main()
