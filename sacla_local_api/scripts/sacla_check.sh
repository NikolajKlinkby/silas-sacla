#!/bin/bash
# Static smoke test of analysis scripts against the SACLA API stubs.
#
#   source sacla_env.sh
#   sacla_check my_script.py [more.py | some_dir ...]   # type-check scripts (mypy)
#   sacla_check --stubtest                              # check the stubs against the compiled modules
#   sacla_check --selftest                              # prove the stubs are active (expects errors)
#
# What mypy catches without running anything: misspelled functions/constants,
# wrong number/order/type of arguments (e.g. a list where the C code needs a tuple),
# misuse of return values (e.g. dict keys that do not exist, tuple unpacking of the
# wrong length), and anything else mypy finds in your own code.
# Target is Python 3.9 (the SACLA cp39 wheels). The HPC default module
# python/SACLA_python-3.7 is Python 3.7: avoid 3.8+ syntax (walrus :=, f"{x=}",
# positional-only "/" in your own defs) if you run there.

set -u
: "${SACLA_LOCAL:?run 'source sacla_env.sh' first}"
PY="$SACLA_LOCAL/venv/bin/python"
MODS="olpy dbpy stpy tmapy ippy ctdapy_xfel ctolpy_xfel"

MYPY_FLAGS=(--python-version 3.9 --ignore-missing-imports --check-untyped-defs
            --warn-unused-ignores --no-error-summary --pretty --show-error-codes)

case "${1:-}" in
  ""|-h|--help)
    sed -n '2,16p' "$0"; exit 0 ;;

  --stubtest)
    # private type-only helpers (_DetInfo, _TagList, ...) exist only in the stubs
    ALLOW=$(mktemp)
    # tmapy constructors: C signature not introspectable (runtime shows *args, **kwargs);
    # the C code accepts no arguments, which is what the stub says
    printf '%s\n' '.*\._[A-Za-z].*' 'tmapy\.Udb(CtrlObject|DataBuffer|ParamBuffer)\.__init__' > "$ALLOW"
    echo "=== stubtest (stubs vs. compiled modules)"
    "$PY" -m mypy.stubtest $MODS --allowlist "$ALLOW" --ignore-missing-stub \
      --ignore-unused-allowlist 2>&1 | grep -vE '^Success' ; rc=${PIPESTATUS[0]}
    rm -f "$ALLOW"
    [ $rc -eq 0 ] && echo "  OK: every stub entry exists in the compiled modules"
    exit $rc ;;

  --selftest)
    T=$(mktemp --suffix=.py)
    cat > "$T" <<'PY'
import numpy as np, olpy, dbpy, stpy, ippy
reader = olpy.StorageReader("MPCCD-8-2-001-1")
buf = olpy.StorageBuffer(reader)
tag: int = reader.collect(buf)
info = buf.read_det_info(0)
x = info["mp_pixelsize"]                                  # E: no such key (it is mp_pixelsize_x)
dbpy.read_syncdatalist_float("sig", 2020, [1, 2, 3])      # E: list, needs tuple
dbpy.read_runinfo(3)                                      # E: missing argument
r = stpy.StorageReader("MPCCD-1", 3, (1,)); stpy.StorageBuffer(r).read_det_info(0)["mp_pixelsize"]  # E
lo, hi = ippy.min_max_avg(np.zeros((4, 4), np.float32))   # E: 3 values, not 2
olpy.read_detidlst()                                      # E: misspelled
PY
    echo "=== selftest: the following 6 errors are EXPECTED"
    OUT=$("$PY" -m mypy "${MYPY_FLAGS[@]}" "$T" 2>&1); echo "$OUT"
    n=$(echo "$OUT" | grep -c ': error:')
    rm -f "$T"
    if [ "$n" -ge 6 ]; then echo "  OK: stubs are active ($n errors found)"; exit 0
    else echo "  !! expected 6 errors, got $n: stubs not found by mypy?"; exit 1; fi ;;

  *)
    "$PY" -m mypy "${MYPY_FLAGS[@]}" "$@"; rc=$?
    [ $rc -eq 0 ] && echo "OK: no problems found in $*"
    exit $rc ;;
esac
