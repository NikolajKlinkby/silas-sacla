# Running and writing scripts

## Running scripts from the session

`run()` starts a script with the session's Python, profile, mode and configuration:

```python
run("scripts/hello_sacla.py")                         # a file (relative to SILAS)
run("silas.offline.logger", "--rebuild")           # a module, with arguments
run("silas.online.logger", new_terminal=True)      # in its own terminal window
```

- **Smoke mode**: the script is only checked with `sacla_check`, not run.
- **Live mode**: the script runs. Without `new_terminal` the session waits until it
  finishes. With `new_terminal=True` it opens a new window (the profile's `terminal`
  command) and the session continues immediately. Online analysis scripts are started this
  way so they keep running in the background.

## Writing a new script

A script needs three things:

1. `import silas` **as the first import**: this applies the profile's Python paths.
2. Read the configuration with `Config()`; never hard-code beamline, run numbers or paths.
3. Put the work in a `main()` marked `@sacla.requires_sacla` if it needs a SACLA library,
   so it is only checked in smoke mode.

Template (save as `silas/online/my_script.py` or `silas/offline/my_script.py`):

```python
"""One line saying what the script does.

    run("silas.online.my_script")          # from the session
    python -m silas.online.my_script       # from a shell
"""
import argparse
from typing import List, Optional

import silas  # noqa: F401  first import: applies the profile's sys.path
from silas.core import db, names, sacla
from silas.core.config import Config


@sacla.requires_sacla
def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", type=int, help="run number (default: newest)")
    args = ap.parse_args(argv)

    cfg = Config()
    bl = cfg.beamline
    run = args.run if args.run is not None else db.newest_run(bl)
    i0 = names.resolve(cfg, "i0")                      # alias -> true name
    values = db.read_run_points(bl, run, [i0])
    print(values.describe())


if __name__ == "__main__":
    main()
```

Scripts outside the `silas` package (like `scripts/hello_sacla.py`) must first add the
SILAS folder to `sys.path` before `import silas`; see that file for the two lines.

## Adding a command to the session

Session commands are defined in `silas/cli/commands.py`, inside `build()`. Add a function
with a docstring (its first line is what `commands()` shows) and add it to the `cmds`
dictionary at the end. It then also appears automatically on the
[Session commands](commands.html) page the next time the documentation is built.
