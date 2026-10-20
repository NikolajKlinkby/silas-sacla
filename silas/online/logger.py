"""Online run logger (DAQ-USER-LAN): catch up from first_run, then follow new runs.

    run("silas.online.logger", new_terminal=True)     # from the session: start_online_logger()
    python -m silas.online.logger [--poll 5] [--first-run N]
"""
import argparse
from typing import List, Optional

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import sacla
from silas.core.config import Config
from silas.core.runlog import RunLogger


@sacla.requires_sacla
def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="online run logger (follows new runs)")
    ap.add_argument("--poll", type=float, default=5.0, help="seconds between checks (default 5)")
    ap.add_argument("--first-run", type=int, help="override the config's first_run")
    args = ap.parse_args(argv)
    RunLogger(Config()).run(follow=True, poll=args.poll, first_run=args.first_run)


if __name__ == "__main__":
    main()
