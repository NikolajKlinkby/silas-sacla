"""Generate SACLA_Software.md – the 'SACLA Software' menu of the SACLA HPC portal.

The menu is read from resources/web/index.html (the portal home page). If that
file is missing, the copy of the menu embedded below is used. Manual links point
to the local PDFs; anything not downloaded links to the website instead.
"""
import html
import re

from common import (MANUALS, MD, PAGE, SITE, WEB, manual_pdf, md_link, read, rel, write)

OUT = MD / PAGE["software"]

# Menu entries that point to pages we recreate
LOCAL_PAGES = {
    "/manuals/modules": PAGE["modules"],
    "/manuals/offline/overview.html": PAGE["overview_offline"],
    "/manuals/online/overview.html": PAGE["overview_online"],
    "/manuals/release.html": PAGE["release"],
    "/manuals/examples": PAGE["examples"],
}
SKIP = {"Share your code"}   # menu entries we leave out

# Fallback: the menu as it was on the portal (Oct 2026)
EMBEDDED_MENU = """
<h2 class="category">Overview</h2><ul>
<li><a href="/manuals/modules">Module List</a></li>
<li><a href="/manuals/offline/overview.html">Offline Analysis Overview</a></li>
<li><a href="/manuals/online/overview.html">Online Analysis Overview</a></li>
<li><a href="/manuals/release.html">Software Release History</a></li></ul>
<h2 class="category">Examples</h2><ul>
<li><a href="/manuals/examples">Examples</a></li>
<li><a href="http://xhpcelog.hpc.spring8.or.jp/form">Share your code</a></li></ul>
<h2 class="category">Tutorials &amp; Slides</h2><ul>
<li><a href="/manuals/offline/#MPCCD_octal_image_assembly_algorithm">MPCCD octal image assembly algorithm</a></li>
<li><a href="/document/Practical_Guide_to_SACLA_Data_Handling.html">Practical Guide to SACLA Data Handling</a></li></ul>
<h2 class="category"><a href="/manuals/offline/index.html">Offline Analysis Software</a></h2><ul>
""" + "".join(f'<li><a href="/manuals/offline/#{a}">{n}</a></li>' for a, n in [
    ("CITIUSDataAccessUserAPI", "CITIUSDataAccessUserAPI(C++)"),
    ("CITIUSDataAccessUserAPIPythonIF", "CITIUSDataAccessUserAPI(Python)"),
    ("CITIUSRunInformationViewer", "CITIUS Run Information Viewer"),
    ("CITIUSShowRunStatus", "CITIUSShowRunStatus"), ("CreateTMAID", "CreateTMAID"),
    ("DataAccessUserAPI", "DataAccessUserAPI(C)"), ("DataAccessUserAPIPythonIF", "DataAccessUserAPI(Python)"),
    ("DataArrayUserAPI", "DataArrayUserAPI"), ("DatabaseViewer", "Database Viewer"),
    ("DataConvert4", "DataConvert4"), ("DataConverterGUI", "DataConverterGUI"),
    ("DeleteTMAID", "DeleteTMAID"), ("DeleteTMAUDB", "DeleteTMAUDB"), ("H5AddDBInfo", "H5AddDBInfo"),
    ("H5AddTMAUDB", "H5AddTMAUDB"), ("HDFAccessUserAPI", "HDFAccessUserAPI"),
    ("ImageProcessingUserAPI", "ImageProcessingUserAPI(C++)"),
    ("ImageProcessingUserAPIPythonIF", "ImageProcessingUserAPI(Python)"), ("ImgAvg", "ImgAvg"),
    ("ImgLA", "ImgLA"), ("MakeTagList", "MakeTagList"), ("OfflineUDBWeb", "Offline-UDB Web"),
    ("OutputTMACsvFromUDB", "OutputTMACsvFromUDB"), ("OutputTMAH5FromUDB", "OutputTMAH5FromUDB"),
    ("RunDataFormatAccessUserAPI", "RunDataFormatAccessUserAPI"), ("RunDataViewer", "RunDataViewer"),
    ("ShowDetIDList", "ShowDetIDList"), ("ShowEquipList", "ShowEquipList"),
    ("ShowNewestRunNumber", "ShowNewestRunNumber"), ("ShowNewestTagNumber", "ShowNewestTagNumber"),
    ("ShowNewTMAID", "ShowNewTMAID"), ("ShowRunInfo", "ShowRunInfo"), ("ShowTMAUDBInfo", "ShowTMAUDBInfo"),
    ("TimingMonitorAnalyzerCUI", "TimingMonitorAnalyzerCUI"),
    ("TimingMonitorAnalyzerGUI", "TimingMonitorAnalyzerGUI"), ("TMAUserAPI", "TMAUserAPI(C++)"),
    ("TMAUserAPIPythonIF", "TMAUserAPI(Python)"), ("UdbCreateAID", "UdbCreateAID"),
    ("UdbDefragAID", "UdbDefragAID"), ("UdbDeleteAID", "UdbDeleteAID"), ("UdbDeleteData", "UdbDeleteData"),
    ("UdbGetNewAID", "UdbGetNewAID"), ("UdbGetSpaceSize", "UdbGetSpaceSize"),
    ("UdbGrantReadAuth", "UdbGrantReadAuth"), ("UdbRevokeReadAuth", "UdbRevokeReadAuth"),
    ("UdbShowGroupList", "UdbShowGroupList"), ("UdbShowInfo", "UdbShowInfo"),
    ("UdbShowUserList", "UdbShowUserList"),
    ("User-Database_Command_UserDatabaseAPI", "User-Database Command, UserDatabaseAPI(C)"),
    ("UserDatabaseAPIPythonIF", "UserDatabaseAPI(Python)"), ("2DDataViewer", "2D Data Viewer")]) + """</ul>
<h2 class="category"><a href="/manuals/online/index.html">Online Analysis Software</a></h2><ul>
""" + "".join(f'<li><a href="/manuals/online/#{a}">{n}</a></li>' for a, n in [
    ("CITIUSOnlineUserAPI", "CITIUSOnlineUserAPI(C++)"), ("CITIUSOnlineUserAPIPython", "CITIUSOnlineUserAPI(Python)"),
    ("CITIUSShowConnectIDInfo", "CITIUSShowConnectIDInfo"), ("CreateTMAID", "CreateTMAID"),
    ("DataAccessUserAPI", "DataAccessUserAPI(C)"), ("DataAccessUserAPIPythonIF", "DataAccessUserAPI(Python)"),
    ("DataArrayUserAPI", "DataArrayUserAPI"), ("DeleteTMAID", "DeleteTMAID"), ("DeleteTMAUDB", "DeleteTMAUDB"),
    ("HDFAccessUserAPI", "HDFAccessUserAPI"), ("ImageProcessingUserAPI", "ImageProcessingUserAPI(C++)"),
    ("ImageProcessingUserAPIPythonIF", "ImageProcessingUserAPI(Python)"), ("OnlineUDBWeb", "Online-UDB Web"),
    ("OnlineUserAPI", "OnlineUserAPI(C)"), ("OnlineUserAPIPythonIF", "OnlineUserAPI(Python)"),
    ("OutputTMACsvFromUDB", "OutputTMACsvFromUDB"), ("OutputTMAH5FromUDB", "OutputTMAH5FromUDB"),
    ("ShowDetIDListOnline", "ShowDetIDListOnline"), ("ShowNewTMAID", "ShowNewTMAID"),
    ("ShowTMAUDBInfo", "ShowTMAUDBInfo"), ("TMAOnlineCUI", "TMAOnlineCUI"), ("TMAOnlineGUI", "TMAOnlineGUI"),
    ("TMAUserAPI", "TMAUserAPI(C++)"), ("TMAUserAPIPythonIF", "TMAUserAPI(Python)"),
    ("UdbCreateAID", "UdbCreateAID"), ("UdbDefragAID", "UdbDefragAID"), ("UdbDeleteAID", "UdbDeleteAID"),
    ("UdbDeleteData", "UdbDeleteData"), ("UdbGetNewAID", "UdbGetNewAID"), ("UdbGetSpaceSize", "UdbGetSpaceSize"),
    ("UdbGrantReadAuth", "UdbGrantReadAuth"), ("UdbRevokeReadAuth", "UdbRevokeReadAuth"),
    ("UdbShowGroupList", "UdbShowGroupList"), ("UdbShowInfo", "UdbShowInfo"),
    ("UdbShowUserList", "UdbShowUserList"),
    ("User-Database_Command_UserDatabaseAPI", "User-Database Command, UserDatabaseAPI(C)"),
    ("UserDatabaseAPIPythonIF", "UserDatabaseAPI(Python)")]) + "</ul>"



def tidy_downloads():
    """Rename finished Chrome downloads: X.pdf.crdownload -> X.pdf (if X.pdf is absent)."""
    renamed = 0
    for f in MANUALS.glob("*/*.pdf.crdownload"):
        target = f.with_name(f.name[: -len(".crdownload")])
        if not target.exists() and f.read_bytes()[-1024:].rstrip().endswith(b"%%EOF"):
            f.rename(target)
            renamed += 1
    return renamed


def read_menu():
    t = read(WEB / "index.html")
    m = re.search(r'<div id="SaclaSoftware".*?(?=<div id="[A-Z]|<footer)', t, re.S)
    return (m.group(0), "resources/web/index.html") if m else (EMBEDDED_MENU, "embedded copy")


def parse_menu(menu):
    sections = []
    for part in re.split(r'(?=<h2 class="category">)', menu)[1:]:
        h = re.search(r'<h2 class="category">(.*?)</h2>', part, re.S).group(1)
        head_href = re.search(r'href="([^"]+)"', h)
        title = html.unescape(re.sub(r"<[^>]+>", "", h)).strip()
        items = [(href, html.unescape(re.sub(r"<[^>]+>", "", txt)).strip())
                 for href, txt in re.findall(r'<li><a href="([^"]+)"[^>]*>(.*?)</a>', part, re.S)]
        sections.append((title, head_href.group(1) if head_href else None, items))
    return sections


def target(href, missing):
    """Relative link for a menu entry: local page, local PDF, or the web URL."""
    if href in LOCAL_PAGES:
        return LOCAL_PAGES[href]
    m = re.match(r"/manuals/(offline|online)/#(.+)", href)
    if m:
        pdf = manual_pdf(m.group(1), m.group(2))
        if pdf:
            return rel(pdf, OUT)
        missing.append(m.group(2))
    return href if href.startswith("http") else SITE + href


def main():
    renamed = tidy_downloads()
    menu, source = read_menu()
    missing = []
    out = ["# SACLA Software", "",
           f"Recreated from the *SACLA Software* section of <{SITE}/#SaclaSoftware> (VPN only).  ",
           "Links point to the local PDF manuals in `resources/manuals/`.  ",
           "Generated by `src/build_software.py` – rebuild with `python3 build_all.py`.", "",
           f"See also: [System – HPC users manual, request forms and links](<{PAGE['system']}>)", ""]
    for title, head_href, items in parse_menu(menu):
        out += [f"## {title}", ""]
        if head_href:
            kind = "Offline" if "offline" in head_href else "Online"
            matrix = MANUALS / kind / f"software_matrix_{kind.lower()}.pdf"
            line = f"Web index: <{SITE}{head_href}>"
            if matrix.is_file():
                line += f" ·  \nSoftware matrix: {md_link(matrix.name, rel(matrix, OUT))}"
            out += [line, ""]
        for href, name in items:
            if name in SKIP:
                continue
            t = target(href, missing)
            note = " (web, VPN only)" if t.startswith("http") else ""
            out.append(f"- {md_link(name, t)}{note}")
        out.append("")
    write(OUT, "\n".join(out))
    print(f"Wrote {PAGE['software']} (menu from {source})"
          + (f"; renamed {renamed} .crdownload files" if renamed else ""))
    if missing:
        print("  Manuals not found locally (linked to the web):", ", ".join(sorted(set(missing))))


if __name__ == "__main__":
    main()
