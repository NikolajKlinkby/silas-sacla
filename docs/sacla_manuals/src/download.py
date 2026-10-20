"""Fetch the SACLA HPC portal pages, release notes, example readmes and PDF manuals.

Needs the SPring-8 VPN and `wget`. Files go to resources/ (see common.py).
    mode "download": only fetch files you don't have yet (wget -nc)
    mode "refresh":  also re-fetch files that are newer on the portal (wget -N)
"""
import re
import shutil
import subprocess
import sys

from common import EXAMPLES, KINDS, MANUALS, ROOT, SITE, WEB, read

PAGES = [
    "/", "/manuals/modules", "/manuals/offline/overview.html", "/manuals/online/overview.html",
    "/manuals/release.html", "/manuals/examples",
    "/manuals/offline/index.html", "/manuals/online/index.html",
    "/enviroment/", "/document/vpn_win.html", "/document/vpn_mac.html",
    "/document/access_win.html", "/document/access_mac.html", "/document/change_password.html",
    "/document/about_filesystem.html", "/document/about_retention.html",
    "/document/about_environment_modules.html", "/document/howto_run_jobs.html",
    "/document/queue_configuration.html", "/faq/", "/assets/PBSUserGuide13.0.pdf",
]
EXTERNAL = [  # kept under resources/web/<host>/...
    "http://xfel.riken.jp/users/pdf/hpc_startup_manual_ver1.8.pdf",
    "http://xfel.riken.jp/users/pdf/20200716_SACLA_data_retention_e.pdf",
]


def wget(urls, clobber, *args):
    if not urls:
        print("    (nothing to fetch)")
        return
    WEB.mkdir(parents=True, exist_ok=True)
    listfile = WEB / "_wget_urls.txt"
    listfile.write_text("\n".join(urls) + "\n", encoding="utf-8")
    cmd = ["wget", clobber, "-e", "robots=off", "--wait=0.2", "-nv", *args, "-i", str(listfile)]
    print(f"    wget {' '.join(cmd[1:-2])} ({len(urls)} urls)")
    subprocess.run(cmd, cwd=ROOT)
    listfile.unlink(missing_ok=True)


def run(mode="download"):
    if not shutil.which("wget"):
        sys.exit("wget not found – install it or download the files by hand.")
    clobber = "-N" if mode == "refresh" else "-nc"
    web = str(WEB.relative_to(ROOT))

    print("  1/4 Portal pages, images and documents")
    wget([SITE + p for p in PAGES], clobber, "-x", "-nH", "-E", "-p", "-P", web)
    wget(EXTERNAL, clobber, "-x", "-P", web)

    print("  2/4 Release notes")
    rel = re.findall(r'href="(/release/[^"]+)"', read(WEB / "manuals" / "release.html"))
    wget([SITE + h for h in dict.fromkeys(rel)], clobber, "-x", "-nH", "-E", "-P", web)

    print("  3/4 Example readmes")
    pages = [WEB / "manuals" / "examples.html", WEB / "manuals" / "examples" / "index.html",
             WEB / "manuals" / "offline" / "index.html", WEB / "manuals" / "online" / "index.html"]
    ex = sorted(set(h for p in pages for h in re.findall(r'href="(/examples/\d+/readme\.txt)"', read(p))))
    wget([SITE + h for h in ex], clobber, "-x", "-nH", "--cut-dirs=1", "-P", str(EXAMPLES.relative_to(ROOT)))

    print("  4/4 PDF manuals linked from the manual index pages")
    for kind in KINDS:
        page = read(WEB / "manuals" / kind.lower() / "index.html")
        pdfs = []
        for h in re.findall(r'href="([^"]+\.pdf)"', page):
            if h.startswith("http"):
                pdfs.append(h)
            elif h.startswith("/"):
                pdfs.append(SITE + h)
            else:
                pdfs.append(f"{SITE}/manuals/{kind.lower()}/{h}")
        wget(list(dict.fromkeys(pdfs)), clobber, "-nd", "-P", str((MANUALS / kind).relative_to(ROOT)))
