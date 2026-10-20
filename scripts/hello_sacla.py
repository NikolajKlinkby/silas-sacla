#!/usr/bin/env python3
"""Minimal example script: shows which profile/config it picked up and lists
the online detector IDs. In smoke mode the olpy call is only type-checked.

    run("scripts/hello_sacla.py")                      # from the session
    run("scripts/hello_sacla.py", new_terminal=True)   # in a new terminal (live mode)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # SILAS
import silas  # noqa: E402  first silas import: applies the profile's sys.path
from silas.core import sacla  # noqa: E402
from silas.core.config import Config  # noqa: E402


@sacla.requires_sacla
def list_detectors() -> None:
    import olpy

    for det_id in olpy.read_detidlist():
        print("  ", det_id)


def main() -> None:
    cfg = Config()
    print("profile:", cfg.profile_name, "| mode:", sacla.mode(), "| config:", cfg.path)
    print("beamline in config:", cfg.beamline)
    list_detectors()


if __name__ == "__main__":
    main()
