# SACLA manuals – local copy

A local, offline copy of the software and HPC documentation from the
[SACLA HPC portal](http://xhpcfep.hpc.spring8.or.jp) (VPN only), as **markdown** and as a **website**.

- Website: open `html/index.html` in a browser.
- Markdown: open `markdown/SACLA_Software.md` (works well in Obsidian).

## Rebuild

```bash
pip install -r requirements.txt          # once (only needed for the html version)
python3 build_all.py                     # rebuild markdown/ and html/ from what is on disk
python3 build_all.py --download          # on the VPN: fetch new/missing files first
python3 build_all.py --refresh           # on the VPN: also re-fetch files that changed on the portal
```

Downloading needs `wget` and the SPring-8 VPN. Everything in `markdown/` and `html/` is
regenerated on every build, so keep your own notes elsewhere.

## Folders

| Folder | Contents |
|---|---|
| `src/` | builder code (`common.py` holds all paths) |
| `resources/manuals/` | PDF manuals (`Offline/`, `Online/`) |
| `resources/examples/` | example readmes (`NNNN/readme.txt`) |
| `resources/web/` | raw copy of the portal pages, images and release notes |
| `markdown/` | generated markdown |
| `html/` | generated website |

All links are relative, so the whole folder can be moved or shared.
The documentation itself belongs to RIKEN / JASRI.
