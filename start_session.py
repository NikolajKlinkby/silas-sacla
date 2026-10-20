#!/usr/bin/env python3
"""Start the SILAS session.

    python start_session.py                     # default profile from config/profiles.json (local)
    python start_session.py --profile beamline  # DAQ-USER-LAN
    python start_session.py --profile hpc       # HPC compute node
    python start_session.py --mode smoke        # override the profile's mode for this session
    python start_session.py --plain             # plain Python prompt even if IPython exists
    python start_session.py --config other.json # another experiment config (default config/<profile>.json)

Use the Python you will analyse with (at SACLA the Intel Python 3.7).
Locally: source LocalAPI/sacla_env.sh first, so smoke tests can use sacla_check.

Arguments are parsed with the stdlib BEFORE the silas package is imported,
because importing it applies the profile's sys.path.
"""
import argparse
import os
import sys


def main() -> None:
    ap = argparse.ArgumentParser(description="SILAS interactive session")
    ap.add_argument("--profile", help="local | beamline | hpc (default: from config file)")
    ap.add_argument("--mode", choices=("smoke", "live"), help="override the profile's mode")
    ap.add_argument("--config", help="experiment config (default: config/<profile>.json)")
    ap.add_argument("--plain", action="store_true", help="do not use IPython")
    args = ap.parse_args()

    if args.profile:
        os.environ["SILAS_PROFILE"] = args.profile
    if args.config:
        os.environ["SILAS_CONFIG"] = os.path.abspath(os.path.expanduser(args.config))
    if args.mode:
        os.environ["SILAS_MODE"] = args.mode

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    import silas  # noqa: F401  (applies the profile: sys.path)
    from silas.cli import shell

    shell.start(use_ipython=not args.plain)


if __name__ == "__main__":
    main()
