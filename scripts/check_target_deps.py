"""Which dependencies of the packages installed in a --target folder are missing for THIS Python?

    PYTHONPATH=~/SILAS_jupyter:~/SILAS_py37 python scripts/check_target_deps.py ~/SILAS_jupyter

Run with the Python that will use the folder (the SACLA Python 3.7). The wheels are downloaded on the
laptop with pip download --python-version 37, but pip evaluates the dependency markers
(e.g. importlib_resources; python_version < "3.9") with the laptop's Python, so backports that only
Python 3.7 needs can be missing. This reads every Requires-Dist of the installed packages, evaluates
its marker with this Python, and looks the requirement up on sys.path (the folder, ~/SILAS_py37
and the SACLA site-packages). Prints the missing ones as a pip download line for the laptop.
Exit code 1 if something is missing.
"""
import os
import pathlib
import sys
from typing import Dict, List, Set, Tuple


def main() -> int:
    target = os.path.abspath(os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/SILAS_jupyter"))
    try:
        import importlib_metadata as md  # backport in the folder (Python 3.7)
    except ImportError:
        import importlib.metadata as md  # type: ignore  # Python >= 3.8
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    installed = {}  # type: Dict[str, str]
    for dist in md.distributions():
        meta = dist.metadata
        name = meta["Name"] if meta is not None else None
        if name:
            installed.setdefault(canonicalize_name(name), dist.version)

    missing = {}  # type: Dict[str, List[str]]
    wrong = []  # type: List[Tuple[str, str, str, str]]
    seen = set()  # type: Set[str]
    for entry in sorted(os.listdir(target)):
        if not entry.endswith(".dist-info"):
            continue
        dist = md.PathDistribution(pathlib.Path(os.path.join(target, entry)))
        meta = dist.metadata
        owner = meta["Name"] if meta is not None else None
        if not owner or canonicalize_name(owner) in seen:
            continue
        seen.add(canonicalize_name(owner))
        for line in dist.requires or []:
            req = Requirement(line)
            if req.marker is not None and not req.marker.evaluate({"extra": ""}):
                continue
            key = canonicalize_name(req.name)
            if key not in installed:
                missing.setdefault(req.name + str(req.specifier), []).append(owner)
            elif req.specifier and not req.specifier.contains(installed[key], prereleases=True):
                wrong.append((owner, req.name, str(req.specifier), installed[key]))

    print("python {} checks {} packages in {}".format(sys.version.split()[0], len(seen), target))
    for owner, name, spec, have in wrong:
        print("  version: {} wants {}{}, found {}".format(owner, name, spec, have))
    if not missing:
        print("  all dependencies found")
        return 0
    for wanted, owners in sorted(missing.items()):
        print("  MISSING {:<40s} needed by {}".format(wanted, ", ".join(sorted(set(owners)))))
    print("on the laptop, add them with:")
    print('  bash scripts/fetch_jupyter_packages.sh {}'.format(" ".join('"{}"'.format(r) for r in sorted(missing))))
    return 1


if __name__ == "__main__":
    sys.exit(main())
