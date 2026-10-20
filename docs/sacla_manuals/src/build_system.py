"""Generate the 'System' menu and the HPC users-manual pages as markdown.

Reads the portal pages in resources/web/ and writes to markdown/:
  SACLA_System.md        the 'System' menu (HPC Users Manual, Request Form, Link)
  System/*.md            one page per documentation page (images stay in resources/web/)
Standard library only.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

from common import MD, PAGE, RESOURCES, SITE, WEB, read, rel, write

OUT = MD / "System"
MENU = MD / PAGE["system"]

# web path -> (markdown file in System/, title)
PAGES = {
    "/enviroment/": ("Data_Analysis_Environment.md", "Data Analysis Environment"),
    "/document/vpn_win.html": ("VPN_Windows.md", "VPN Connection Manual (Windows)"),
    "/document/vpn_mac.html": ("VPN_Mac.md", "VPN Connection Manual (Mac)"),
    "/document/access_win.html": ("Access_Windows.md", "How to access (Windows)"),
    "/document/access_mac.html": ("Access_Mac.md", "How to access (Mac)"),
    "/document/change_password.html": ("Change_Password.md", "How to change password"),
    "/document/about_filesystem.html": ("Filesystem.md", "About filesystem"),
    "/document/about_retention.html": ("Data_Retention.md", "Data retention policy"),
    "/document/about_environment_modules.html": ("Environment_Modules.md", "Environment Modules"),
    "/document/howto_run_jobs.html": ("Run_Jobs.md", "How to run jobs"),
    "/document/queue_configuration.html": ("Queue_Configuration.md", "Queue configuration"),
    "/faq/": ("FAQ.md", "FAQ"),
}
# other targets that exist locally
LOCAL = {
    "/manuals/modules": MD / PAGE["modules"],
    "/assets/PBSUserGuide13.0.pdf": WEB / "assets" / "PBSUserGuide13.0.pdf",
    "http://xfel.riken.jp/users/pdf/hpc_startup_manual_ver1.8.pdf":
        WEB / "xfel.riken.jp" / "users" / "pdf" / "hpc_startup_manual_ver1.8.pdf",
    "http://xfel.riken.jp/users/pdf/20200716_SACLA_data_retention_e.pdf":
        WEB / "xfel.riken.jp" / "users" / "pdf" / "20200716_SACLA_data_retention_e.pdf",
}


def local_file(web_path):
    p = web_path.split("#")[0]
    if p.endswith("/"):
        p += "index.html"
    return WEB / p.lstrip("/")


def resolve(url, from_file):
    """Map a link from the website to a relative local link if we have it."""
    base, _, frag = url.partition("#")
    if base in PAGES:
        return rel(OUT / PAGES[base][0], from_file)
    if base in LOCAL and (LOCAL[base].is_file() or LOCAL[base].suffix == ".md"):
        return rel(LOCAL[base], from_file)
    if base.startswith("/assets/") and (WEB / base.lstrip("/")).is_file():
        return rel(WEB / base.lstrip("/"), from_file)
    if base.startswith("/"):
        return SITE + url
    return url


KNOWN = {"p","div","h1","h2","h3","h4","h5","h6","blockquote","ul","ol","li","table","thead","tbody","tr","td","th","br","strong","b","em","i","code","a","img","pre","span","font","u","sup","sub","hr","script","style","small","big","article","header","nav","section","time","label","dl","dt","dd"}


class MDConverter(HTMLParser):
    """Small HTML -> Markdown converter for the Jekyll pages of the portal."""

    def __init__(self, from_file):
        super().__init__(convert_charrefs=True)
        self.d = from_file
        self.out = []          # finished blocks
        self.buf = []          # current inline text
        self.lists = []        # stack of ["ul"|"ol", counter]
        self.pre = False
        self.pre_buf = []
        self.href = []
        self.table = None      # list of rows, row = list of cells
        self.cell = None
        self.quote = 0
        self.card_q = False    # FAQ question header
        self.skip = 0

    # -- helpers
    def flush(self):
        text = re.sub(r"[ \t\r\n]+", " ", "".join(self.buf)).strip()
        self.buf = []
        if not text:
            return
        if self.lists:
            depth = len(self.lists) - 1
            kind = self.lists[-1]
            if kind[2]:   # first paragraph of this item gets the bullet
                bullet = f"{kind[1]}." if kind[0] == "ol" else "-"
                kind[2] = False
                text = "    " * depth + f"{bullet} " + text
            else:
                text = "    " * depth + "    " + text
        self.emit(text)

    def emit(self, block):
        if self.quote:
            block = "\n".join("> " + l if l else ">" for l in block.split("\n"))
        self.out.append(block)

    # -- parser callbacks
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        if self.skip:
            return
        if tag not in KNOWN:          # e.g. "<job_name>" placeholders in the text
            (self.pre_buf if self.pre else self.cell if self.cell is not None else self.buf).append(
                f"<{tag}>" if self.pre else f"&lt;{tag}&gt;")
            return
        if self.pre:
            return
        if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "ul", "ol", "table") \
                and self.cell is None:
            self.flush()
        if tag == "div" and "card-header" in a.get("class", ""):
            self.card_q = True
            self.buf.append("#### ")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.buf.append("#" * int(tag[1]) + " ")
        elif tag == "br":
            if self.cell is not None:
                self.cell.append("<br>")
            else:
                self.buf.append("  \n")
        elif tag in ("strong", "b"):
            self._inline("**")
        elif tag in ("em", "i") and not a.get("class"):
            self._inline("*")
        elif tag == "code":
            self._inline("`")
        elif tag == "a":
            self.href.append(a.get("href", ""))
            self._inline("[")
        elif tag == "img":
            src = resolve(a.get("src", ""), self.d)
            self._inline(f"![{a.get('alt', '')}](<{src}>)")
        elif tag == "pre":
            self.flush()
            self.pre, self.pre_buf = True, []
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0, False])
        elif tag == "li":
            self.flush()
            if self.lists:
                self.lists[-1][1] += 1
                self.lists[-1][2] = True
        elif tag == "blockquote":
            self.quote += 1
        elif tag == "table":
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.table.append([])
        elif tag in ("td", "th") and self.table is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
            return
        if self.skip or tag not in KNOWN:
            return
        if self.pre:
            if tag == "pre":
                code = "".join(self.pre_buf).rstrip("\n")
                lines = code.split("\n")
                indent = min((len(l) - len(l.lstrip()) for l in lines if l.strip()), default=0)
                code = "\n".join(l[indent:] for l in lines)
                pad = "    " * len(self.lists)
                block = "\n".join(pad + l for l in ["```text", *code.split("\n"), "```"])
                self.emit(block)
                self.pre = False
            return
        if tag in ("strong", "b"):
            self._inline("**")
        elif tag == "em":
            self._inline("*")
        elif tag == "code":
            self._inline("`")
        elif tag == "a":
            href = self.href.pop() if self.href else ""
            self._inline(f"](<{resolve(href, self.d)}>)" if href else "]")
        elif tag in ("td", "th") and self.cell is not None:
            txt = re.sub(r"\s+", " ", "".join(self.cell)).strip().replace("|", "\\|")
            self.table[-1].append(txt or " ")
            self.cell = None
        elif tag == "table" and self.table is not None:
            rows = [r for r in self.table if r]
            self.table = None
            if rows:
                n = max(len(r) for r in rows)
                rows = [r + [" "] * (n - len(r)) for r in rows]
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * n]
                lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self.emit("\n".join(lines))
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li") and self.cell is None:
            self.flush()
            if tag == "div" and self.card_q:
                self.card_q = False
        elif tag in ("ul", "ol"):
            self.flush()
            if self.lists:
                self.lists.pop()
        elif tag == "blockquote":
            self.flush()
            self.quote -= 1

    def _inline(self, s):
        if self.cell is not None:
            self.cell.append(s)
        else:
            self.buf.append(s)

    def handle_data(self, data):
        if self.skip:
            return
        if self.pre:
            self.pre_buf.append(data)
        elif self.cell is not None:
            self.cell.append(data)
        else:
            self.buf.append(data)

    def result(self):
        self.flush()
        is_list = lambda line: bool(re.match(r"\s*(?:[-*]|\d+\.) |\s{4}", line))
        parts = []
        for b in (b for b in self.out if b.strip()):
            # consecutive list items (and their indented content) form one list
            if parts and is_list(parts[-1].split("\n")[-1]) and re.match(r"\s*(?:[-*]|\d+\.) ", b):
                parts[-1] += "\n" + b
            else:
                parts.append(b)
        return "\n\n".join(parts).strip() + "\n"


def convert(web_path, md_name, title):
    src = local_file(web_path)
    if not src.is_file():
        return None
    t = read(src)
    i = t.find('<div class="markdown-body">')
    body = t[i:t.find("<footer")] if i >= 0 else t
    body = re.sub(r"<h1>.*?</h1>", "", body, count=1, flags=re.S)   # we write our own title
    out_file = OUT / md_name
    p = MDConverter(out_file)
    p.feed(body)
    head = [f"# {title}", "",
            f"Recreated from <{SITE}{web_path}> (VPN only).  ",
            "Generated by `src/build_system.py` – rebuild with `python3 build_all.py`.", "",
            f"[← Back to System](<{rel(MENU, out_file)}>)", "", ""]
    return "\n".join(head) + p.result()


def link(text, target_web, local=None):
    if target_web in PAGES:
        return f"[{text}](<{rel(OUT / PAGES[target_web][0], MENU)}>)"
    if local is not None and local.is_file():
        return f"[{text}](<{rel(local, MENU)}>)"
    return f"[{text}]({target_web if target_web.startswith('http') else SITE + target_web})"


def build_menu():
    pg = lambda p, t=None: link(t or PAGES[p][1], p)
    startup = "http://xfel.riken.jp/users/pdf/hpc_startup_manual_ver1.8.pdf"
    pbs = "/assets/PBSUserGuide13.0.pdf"
    lines = [
        "# System", "",
        f"Recreated from the *System* section of <{SITE}/#System> (VPN only).  ",
        "Generated by `src/build_system.py` – rebuild with `python3 build_all.py`. Web apps need the VPN.", "",
        f"See also: [SACLA Software](<{PAGE['software']}>)", "",
        "## HPC Users Manual", "",
        f"- {pg('/enviroment/')}",
        f"- {link('HPC-Startup-Manual', startup, LOCAL[startup])} (PDF)", "",
        f"- VPN Connection Manual ({pg('/document/vpn_win.html', 'Win')}, {pg('/document/vpn_mac.html', 'Mac')})",
        f"- How to access ({pg('/document/access_win.html', 'Win')}, {pg('/document/access_mac.html', 'Mac')})",
        f"- {pg('/document/change_password.html')}",
        f"- {pg('/document/about_filesystem.html')}",
        f"- {pg('/document/about_retention.html')}",
        f"- {pg('/document/about_environment_modules.html')}",
        f"- {pg('/document/howto_run_jobs.html')}",
        f"- {pg('/document/queue_configuration.html')}", "",
        f"- {link('PBSUserGuide13.0', pbs, LOCAL[pbs])} (PDF)", "",
        "## Request Form", "",
        "- [HPC Account](http://xhpcelog.hpc.spring8.or.jp/account/login)",
        "- UserDatabase Account *(under construction)*",
        "- Computing Node Occupation *(under construction)*",
        "- Tape Optimized Recall *(under construction)*", "",
        "## Link", "",
        "- [HPC Account Management](http://xhpcelog.hpc.spring8.or.jp/account/)",
        "- [Database Viewer](http://xhpcelog.hpc.spring8.or.jp/analysis/DatabaseViewer)",
        "- [2D Data Viewer](http://xhpcelog.hpc.spring8.or.jp/analysis/2DDataViewer)",
        "- [Offline-UDB Web](http://xhpcelog.hpc.spring8.or.jp/analysis/OfflineUDB)",
        "- [Online-UDB Web (USER-LAN)](http://xu-webserver01.user.xfel.cntl.local/analysis/OnlineUDB/)",
        "- [OpenOndemand](http://xhpcondemand.hpc.spring8.or.jp)",
        "- [e-Log](http://xhpcelog.hpc.spring8.or.jp/elog/)", "",
        "- [Run Information Viewer (BL1)](http://xhmydb01.hpc.spring8.or.jp/cgi-bin/storage/run.cgi?bl=1)",
        "- [Run Information Viewer (BL2)](http://xhmydb01.hpc.spring8.or.jp/cgi-bin/storage/run.cgi?bl=2)",
        "- [Run Information Viewer (BL3)](http://xhmydb01.hpc.spring8.or.jp/cgi-bin/storage/run.cgi?bl=3)",
        "- [XDAQ SyncDAQ](http://xhmydb01.hpc.spring8.or.jp/syncdaq/xdaq.html)", "",
        f"- {pg('/faq/')}", "",
    ]
    return "\n".join(lines)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    missing = []
    for web_path, (md_name, title) in PAGES.items():
        md = convert(web_path, md_name, title)
        if md is None:
            missing.append(web_path)
            continue
        write(OUT / md_name, md)
    write(MENU, build_menu())
    print(f"Wrote {PAGE['system']} and {len(PAGES) - len(missing)} pages in System/")
    if missing:
        print("  Not downloaded yet:", ", ".join(missing))
    absent = [u for u, p in LOCAL.items() if p.suffix == ".pdf" and not p.is_file()]
    if absent:
        print("  Linked to the web because no local copy exists:", ", ".join(absent))


if __name__ == "__main__":
    main()
