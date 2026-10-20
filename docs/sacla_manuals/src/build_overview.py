"""Generate the 'Overview' pages of the SACLA Software menu.

Reads the portal pages in resources/web/ and writes to markdown/:
  SACLA_Modules.md            Module List
  SACLA_Overview_Offline.md   Offline Analysis Overview (diagram + linked list)
  SACLA_Overview_Online.md    Online Analysis Overview  (diagram + linked list)
  SACLA_Release_History.md    Software Release History
  Overview/*.svg              diagrams whose boxes link to the local PDFs (with tooltips)
"""
import html
import re
from collections import OrderedDict

from common import (MD, PAGE, SITE, WEB, header, manual_by_name, manual_description, manual_pdf,
                    md_link, read, rel, strip_tags, write)

BOX_TYPE = {  # fill colour of a box in the diagram -> tool type (diagram legend)
    "#663300": "CUI", "#9a0079": "GUI", "#ff9900": "Web", "#004c99": "OpenOnDemand", "#000000": "API",
}
BACK = ("Back to SACLA Software", MD / PAGE["software"])


# ---------------------------------------------------------------- overview diagrams
def python_pdf(kind, anchor):
    for cand in (f"{anchor}PythonIF", f"{anchor}Python", "UserDatabaseAPIPythonIF"
                 if anchor == "User-Database_Command_UserDatabaseAPI" else None):
        if cand:
            p = manual_pdf(kind, cand)
            if p and p != manual_pdf(kind, anchor):
                return p
    return None


def parse_svg(svg_text):
    s = re.sub(r"<foreignObject.*?</foreignObject>", "", svg_text, flags=re.S)
    texts = [(float(x), float(y), html.unescape(t))
             for x, y, t in re.findall(r'<text x="([\d.]+)" y="([\d.]+)"[^>]*>([^<]*)</text>', s)]
    groups = []
    for x, y, w, h in re.findall(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"[^>]*stroke-dasharray', s):
        x, y, w, h = map(float, (x, y, w, h))
        lab = [t for t in texts if x <= t[0] <= x + w and y - 16 <= t[1] <= y + 2]
        name = (lab[0][2] if lab else "Other").replace("(read on...", "(read only)")
        groups.append({"x": x, "y": y, "w": w, "h": h, "name": name, "items": []})
    groups.sort(key=lambda g: (g["y"], g["x"]))
    sections = [(float(y), html.unescape(t)) for y, t in re.findall(
        r'font-weight="bold"[^>]*font-size="(?:1[6-9]|2\d)px"[^>]*>\s*<text x="[\d.]+" y="([\d.]+)"[^>]*>([^<]*)</text>', s)]
    boxes = []
    for anchor, body in re.findall(r'<a xlink:href="index\.html#([^"]+)"[^>]*>(.*?)</a>', s, re.S):
        r = re.findall(r'<rect x="([\d.]+)" y="([\d.]+)"[^>]*fill="(#[0-9a-fA-F]{6})"', body)
        label = html.unescape(" ".join(t.strip() for t in re.findall(r"<text[^>]*>([^<]*)</text>", body)))
        x, y, fill = (float(r[-1][0]), float(r[-1][1]), r[-1][2].lower()) if r else (0, 0, "")
        boxes.append({"anchor": anchor, "label": label, "x": x, "y": y, "type": BOX_TYPE.get(fill, "")})
    for b in boxes:
        for g in groups:
            if g["x"] <= b["x"] <= g["x"] + g["w"] and g["y"] <= b["y"] <= g["y"] + g["h"]:
                g["items"].append(b)
                break
    for g in groups:
        g["items"].sort(key=lambda b: (b["x"], b["y"]))
    return groups, sections


def local_svg(kind, svg_text, svg_path):
    """Diagram copy whose boxes open the local PDFs and show the manual description on hover."""
    def relink(m):
        anchor, rest = m.group(1), m.group(2)
        pdf = manual_pdf(kind, anchor)
        href = rel(pdf, svg_path) if pdf else f"{SITE}/manuals/{kind.lower()}/#{anchor}"
        tip = manual_description(kind, anchor) or anchor
        return f'<a xlink:href="{html.escape(href)}"{rest}><title>{html.escape(tip)}</title>'
    return re.sub(r'<a xlink:href="index\.html#([^"]+)"([^>]*)>', relink, svg_text)


def build_overview(kind, page_key, svg_name, extra=""):
    out_file = MD / PAGE[page_key]
    title = f"{kind} Analysis Overview"
    svg = read(WEB / "manuals" / kind.lower() / svg_name)
    if not svg:
        return None
    svg_path = MD / "Overview" / svg_name
    write(svg_path, local_svg(kind, svg, svg_path))
    groups, sections = parse_svg(svg)

    out = header(title, f"{SITE}/manuals/{kind.lower()}/overview.html", "build_overview.py",
                 back=BACK, from_file=out_file)
    svg_rel = rel(svg_path, out_file)
    out += [f"![{title}](<{svg_rel}>)", "",
            f'<span class="obsidian-only">*In Obsidian the picture is not clickable – use the list below, or open '
            f"[the diagram](<{svg_rel}>) or the html version, where every box opens its manual.*</span>", ""]
    if extra:
        out += [extra, ""]
    current = None
    for g in groups:
        if not g["items"]:
            continue
        prev = [t for y, t in sections if y <= g["y"]]
        sec = prev[-1] if prev else ""
        if sec and sec != current:
            out += [f"## {sec}", ""]
            current = sec
        out += [f"### {g['name']}", ""]
        for b in g["items"]:
            pdf = manual_pdf(kind, b["anchor"])
            typ = f" `{b['type']}`" if b["type"] else ""
            name = md_link(b["label"], rel(pdf, out_file)) if pdf else b["label"]
            more = "" if pdf else " *(no local manual)*"
            py = python_pdf(kind, b["anchor"]) if "Python" in b["label"] else None
            if py:
                more += f" · {md_link('Python', rel(py, out_file))}"
            if b["anchor"] == "User-Database_Command_UserDatabaseAPI" and "API" not in b["label"]:
                more += " *(User-Database command reference)*"
            out.append(f"- {name}{typ}{more}")
        out.append("")
    out += ["**Legend:** `CUI` command-line tool · `GUI` desktop GUI · `Web` web app · "
            "`OpenOnDemand` runs in Open OnDemand · `API` library", ""]
    write(out_file, "\n".join(out))
    return out_file


# ---------------------------------------------------------------- module list
def build_modules():
    t = read(WEB / "manuals" / "modules.html")
    if "<h1>Module List</h1>" not in t:
        return None
    out_file = MD / PAGE["modules"]
    body = t[t.index("<h1>Module List</h1>"):t.index("<footer")]
    out = header("Module List", f"{SITE}/manuals/modules", "build_overview.py", back=BACK, from_file=out_file)
    for part in re.split(r"(?=<h2)", body)[1:]:
        out += [f"## {strip_tags(re.search(r'<h2[^>]*>(.*?)</h2>', part, re.S).group(1))}", ""]
        p = re.search(r"<p>(.*?)</p>", part, re.S)
        if p:
            out += [strip_tags(p.group(1)), ""]
        out += ["| Module | Description |", "|---|---|"]
        for mod, desc in re.findall(r"<tr>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*</tr>", part, re.S):
            desc = re.sub(r"</?u>", "*", re.sub(r"<br\s*/?>", "<br>", desc))
            out.append(f"| `{html.unescape(mod.strip())}` | {html.unescape(desc.strip())} |")
        out.append("")
    out += ["Load a module with e.g. `module load SACLA_tool/offline` "
            "(the examples use the short form `module load SACLA_tool`).", ""]
    write(out_file, "\n".join(out))
    return out_file


# ---------------------------------------------------------------- release history
def note_text(path):
    m = re.search(r'<div class="post-content">(.*?)</article>', read(path), re.S)
    body = m.group(1) if m else ""
    body = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>|</tr>|</div>", "\n", body)
    body = re.sub(r"<li[^>]*>", "- ", body)
    body = html.unescape(re.sub(r"<[^>]+>", "", body))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l.strip() for l in body.splitlines())).strip()


def build_release_history():
    t = read(WEB / "manuals" / "release.html")
    items = re.findall(r'<li>\s*<a href="([^"]+)">\s*(\S+)\s*(.*?)</a>\s*</li>', t, re.S)
    if not items:
        return None, 0, 0
    out_file = MD / PAGE["release"]
    by_year = OrderedDict()
    have = 0
    for href, date, rest in items:
        labels = re.findall(r'<span class="badge[^"]*">(.*?)</span>', rest)
        text = strip_tags(re.sub(r'<span class="badge[^"]*">.*?</span>', "", rest))
        local = WEB / href.lstrip("/")
        by_year.setdefault(date[:4], []).append((date, labels, text, href, local))
        have += local.is_file()

    out = header("Software Release History", f"{SITE}/manuals/release.html", "build_overview.py",
                 back=BACK, from_file=out_file)
    out += [f"{len(items)} releases. Release notes downloaded locally: {have}/{len(items)}.", ""]
    if have < len(items):
        out += ["> [!tip] Run `python3 build_all.py --download` (on the VPN) to fetch the release notes.", ""]
    for year, rows in by_year.items():
        out.append(f"> [!example]- {year} ({len(rows)} releases)")
        for date, labels, text, href, local in rows:
            tags = " ".join(f"`{l}`" for l in labels)
            first = text.split(" ")[0]
            pdf = manual_by_name(labels[0] if labels else "Offline", first)
            if pdf:
                text = md_link(first, rel(pdf, out_file)) + text[len(first):]
            note = note_text(local) if local.is_file() else ""
            if note:
                out.append(f"> > [!note]- {date} {tags} {text}")
                out += [f"> > {l}".rstrip() for l in note.splitlines()]
                out += [f"> > {md_link('release note (html)', rel(local, out_file))}", ">"]
            else:
                target = rel(local, out_file) if local.is_file() else f"{SITE}{href}"
                out.append(f"> - {date} {tags} {text} – {md_link('note', target)}")
        out += ["", ""]
    write(out_file, "\n".join(out))
    return out_file, have, len(items)


def main():
    done = []
    if build_modules():
        done.append(PAGE["modules"])
    mpccd = manual_pdf("Offline", "MPCCD_octal_image_assembly_algorithm")
    extra = ("**MPCCD octal image assembly algorithm** – sensor position calculation, signal calibration: "
             + md_link("MPCCD octal image assembly algorithm", rel(mpccd, MD / PAGE["overview_offline"]))
             ) if mpccd else ""
    if build_overview("Offline", "overview_offline", "offline_overview.svg", extra):
        done.append(PAGE["overview_offline"])
    if build_overview("Online", "overview_online", "online_overview.svg"):
        done.append(PAGE["overview_online"])
    f, have, total = build_release_history()
    if f:
        done.append(f"{PAGE['release']} (release notes local: {have}/{total})")
    print("Wrote " + ", ".join(done) if done else "Nothing written – portal pages not downloaded yet")


if __name__ == "__main__":
    main()
