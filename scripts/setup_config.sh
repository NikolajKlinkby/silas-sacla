#!/bin/bash
# Set up the experiment config of a profile (silas/core/setup_config.py): the rough default, or
# the standard setup of a beamline. Asks for what is not given (first run, photon energy, output
# folder); an existing config is replaced only after asking (a .bak copy is kept).
#
#   bash scripts/setup_config.sh                         # rough default (beamline 3, no detectors)
#   bash scripts/setup_config.sh --beamline 3            # BL3: detectors, MPCCD arrays, scan settings
#   bash scripts/setup_config.sh --beamline 3 --first-run 1234400 --photon-energy 9.9 --output pcmo_2026
#   bash scripts/setup_config.sh --profile beamline ...  # another profile's config (default: as the session chooses)
#   bash scripts/setup_config.sh --yes ...               # ask nothing (defaults; replaces an existing config)
#
# The same in the session: setup_config(beamline=3, first_run=..., photon_energy_keV=..., output=...)

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-}" in -h|--help) sed -n '2,13p' "$0"; exit 0 ;; esac
PY="${SILAS_PYTHON:-}"
pyok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' 2>/dev/null; }
if [ -z "$PY" ]; then
  # at SACLA: the SACLA Python 3.7 (any node: the config needs no SACLA library); elsewhere python3
  if [ "$(python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" != "3.7" ]; then
    type module >/dev/null 2>&1 || source /etc/profile.d/modules.sh 2>/dev/null
    type module >/dev/null 2>&1 && module load python/SACLA_python-3.7/offline >/dev/null 2>&1
  fi
  if pyok python; then PY=python; else PY=python3; fi
fi
cd "$ROOT" || exit 1
exec "$PY" -m silas.core.setup_config "$@"
