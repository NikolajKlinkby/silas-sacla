"""Offline run logger (HPC-LAN): (re)create the run log from first_run, then stop or follow.

    python -m silas.offline.logger              # add runs missing from the log
    python -m silas.offline.logger --rebuild    # recreate the log from first_run
    python -m silas.offline.logger --last-run N # stop at run N
    python -m silas.offline.logger --follow     # keep following like the online logger (with
                                                # --last-run: until run N is logged and stopped)
    python -m silas.offline.logger --workers 6  # processes reading the runs (default 6)
    qsub ~/SILAS/scripts/runlog_job.sh          # as a batch job (follows); run_logger_status()
"""
import argparse
from typing import List, Optional

import silas  # noqa: F401  first silas import: applies the profile's sys.path
from silas.core import runner, sacla
from silas.core.config import Config
from silas.core.runlog import RunLogger


@sacla.requires_sacla
def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="offline run logger")
    ap.add_argument("--rebuild", action="store_true", help="discard the existing log and start from first_run")
    ap.add_argument("--first-run", type=int, help="override the config's first_run")
    ap.add_argument("--last-run", type=int, help="stop at this run")
    ap.add_argument("--follow", action="store_true", help="keep following new runs")
    ap.add_argument("--poll", type=float, default=5.0, help="seconds between checks with --follow")
    ap.add_argument("--workers", type=int, default=runner.OFFLINE_WORKERS,
                    help="processes reading the runs (default {}; 1: one after the other)".format(
                        runner.OFFLINE_WORKERS))
    args = ap.parse_args(argv)
    RunLogger(Config()).run(follow=args.follow, poll=args.poll, rebuild=args.rebuild,
                            first_run=args.first_run, last_run=args.last_run, workers=args.workers)


if __name__ == "__main__":
    main()
