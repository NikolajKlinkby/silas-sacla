#!/usr/bin/env python3
"""Build <module>-stubs/__init__.pyi packages from src/<module>.pyi + the module's
int constants. Constants are read from the compiled module when it imports
(run inside the SACLA venv), else from constants/<module>.txt."""
import importlib, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
MODULES = ["olpy", "dbpy", "stpy", "tmapy", "ippy", "ctdapy_xfel", "ctolpy_xfel", "udbpy"]
out_root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "build"
for mod in MODULES:
    body = (HERE / "src" / f"{mod}.pyi").read_text()
    try:
        m = importlib.import_module(mod)
        consts = {k: getattr(m, k) for k in dir(m) if not k.startswith("_") and type(getattr(m, k)) is int}
        (HERE / "constants" / f"{mod}.txt").write_text("".join(f"{k} = {v}\n" for k, v in sorted(consts.items())))
        src = "runtime"
    except Exception:
        lines = (HERE / "constants" / f"{mod}.txt").read_text().split("\n")
        consts = {}
        for l in lines:
            if "=" in l:
                k, v = l.split("=", 1); v = v.strip()
                consts[k.strip()] = int(v) if v.lstrip("-").isdigit() else None
        src = "constants file"
    block = "\n# ---- integer constants of the compiled module (values for reference) ----\n"
    block += "".join(f"{k}: int{f'  # = {v}' if v is not None else ''}\n" for k, v in sorted(consts.items()))
    d = out_root / f"{mod}-stubs"; d.mkdir(parents=True, exist_ok=True)
    (d / "__init__.pyi").write_text(body.rstrip() + "\n" + block)
    print(f"  {mod}-stubs  ({len(consts)} constants from {src})")
