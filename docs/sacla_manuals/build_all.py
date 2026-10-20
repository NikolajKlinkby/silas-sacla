#!/usr/bin/env python3
"""Build a local copy of the SACLA HPC portal documentation – as markdown and as html.

    python3 build_all.py               rebuild markdown/ and html/ from what is on disk
    python3 build_all.py --download    first fetch new/missing files (needs VPN + wget)
    python3 build_all.py --refresh     like --download, but also re-fetch files that are
                                       newer on the portal than your copy
    python3 build_all.py --no-html     only build the markdown

Folders:
    src/         the builder code              resources/   downloaded manuals, examples, web pages
    markdown/    generated markdown (Obsidian)  html/        generated website – open html/index.html

Requires Python 3.8+ and, for the html version:  pip install markdown-it-py
"""
import argparse
import importlib
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))
sys.dont_write_bytecode = True   # keep the folder free of __pycache__

BUILDERS = ["build_overview", "build_examples", "build_system", "build_software"]


def step(name):
    print(f"[{name}]", end=" ", flush=True)
    try:
        importlib.import_module(name).main()
    except Exception as e:  # keep going so one broken page doesn't stop the rest
        print(f"FAILED: {e!r}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--download", action="store_true", help="fetch new/missing files first (VPN + wget)")
    ap.add_argument("--refresh", action="store_true", help="also re-fetch files that are newer on the portal")
    ap.add_argument("--no-html", action="store_true", help="skip the html version")
    args = ap.parse_args()

    if args.download or args.refresh:
        print("Downloading" + (" (refresh)" if args.refresh else ""))
        importlib.import_module("download").run("refresh" if args.refresh else "download")
    for name in BUILDERS:
        step(name)
    if not args.no_html:
        step("render_html")
    print("Done. Markdown: markdown/SACLA_Software.md · Website: html/index.html")


if __name__ == "__main__":
    main()
