"""Generate SACLA_Examples.md (Obsidian callouts) from resources/examples/<nr>/readme.txt.

Titles and keyword tags come from the portal's Examples page
(resources/web/manuals/examples.html) when it has been downloaded, otherwise
from the copy embedded below.
"""
import re

from common import EXAMPLES, MD, PAGE, SITE, WEB, header, md_link, read, rel, strip_tags, write

OUT = MD / PAGE["examples"]

# Title and keyword badges as listed on
# http://xhpcfep.hpc.spring8.or.jp/manuals/examples (VPN only)
FALLBACK = {
    "0001": ('example of "MakeTagList"+"DataConvert4"', ["Read SyncDB data", "Tag selection with shutter condition"]),
    "0002": ('example of "MakeTagList"+"DataConvert4" - H5 format of the default', ["Run data format"]),
    "0003": ('example of "MakeTagList"+"DataConvert4" - specify detector-ID', ["Run data format", "Detector specification", "MPCCD single sensor"]),
    "0004": ('example of "ShowRunInfo"+"ShowDetIDList"+"MakeTagList"+"DataConvert4" - output reconstruction image of MPCCD', ["Run data format", "Tag selection with shutter condition", "MPCCD octal sensor", "Assembled Image"]),
    "0005": ('example of "ShowRunInfo"+"ShowDetIDList"+"MakeTagList"+"DataConvert4" - output each image of MPCCD', ["Run data format", "Tag selection with shutter condition", "MPCCD octal sensor", "dual sensor", "Separated Image"]),
    "0006": ('example of "MakeTagList"+"ImgAvg"', ["Background", "Image Average", "Tag selection with shutter condition"]),
    "0007": ('example of background subtraction using "DataConvert4"', ["Background subtraction"]),
    "0008": ('example of "ImgLA"', ["Image subtraction"]),
    "0009": ('example of "DataAccessUserAPI(Python)"', ["Python", "Modification of 1D data in HDF5 file", "Linear Arithmetic"]),
    "0010": ('example of "ShowRunInfo"+"MakeTagList"+"DataConvert4" - output reconstruction image of MPCCD', ["Run data format", "Tag selection with shutter condition and beam status", "MPCCD octal sensor", "Assembled Image", "Serial job submission", "Archive"]),
    "0011": ('example of "MakeTagList"+"DataAccessUserAPI(Python)"', ["Python", "Extraction of the specified signal value", "tag list"]),
    "0012": ('example of "ShowRunInfo"+"MakeTagList"+"DataConvert4" - output reconstruction image of MPCCD', ["Run data format", "Tag selection with shutter condition and beam status", "MPCCD octal sensor", "Assembled Image", "Semi-Automatic job submission", "Archive", "future run number"]),
    "0013": ('example of "TimingMonitorAnalyzerCUI"', ["Arrival-timing monitor", "Semi-Automatic job submission"]),
    "0014": ('example of "EditTagList"', ["Edit tag list file", "MakeTagList", "Python"]),
    "0015": ('example of "OnlineUserAPI(Python)"', ["Python", "OnlineDataViewer", "ImageProcessingUserAPI(Python)", "PyQt"]),
    "0016": ('example of "DataAccessUserAPI(Python)"', ["Python", "Extraction of the specified signal value in SyncDB from time"]),
    "0017": ('example of "ImageProcessingUserAPI(Python)"', ["ImageProcessing", "Python", "ROI"]),
    "0018": ('example of "ImageProcessingUserAPI(Python)"', ["ImageProcessing", "Python", "Binning"]),
    "0019": ('example of "ImageProcessingUserAPI(Python):ippy.divided_ring_roi_average"', ["ImageProcessing", "Python", "Divided Ring ROI Average"]),
    "0020": ('example of "ImageProcessingUserAPI(Python):ippy.linear_arithmetic"', ["ImageProcessing", "Python", "Linear Arithmetic"]),
    "0021": ('example of "ImageProcessingUserAPI(Python)"', ["ImageProcessing", "Python", "Threshold"]),
    "0022": ('example of "ImageProcessingUserAPI(Python)"', ["ImageProcessing", "Python", "Minimum", "Maximum", "Average"]),
    "0023": ('example of "ImageProcessingUserAPI(Python)"', ["ImageProcessing", "Python", "Histogram"]),
    "0024": ('example of "ImageProcessingUserAPI(Python)"', ["ImageProcessing", "Python", "Intensity-weighted Centroid"]),
    "0025": ('example of "ImageProcessingUserAPI(Python)"', ["ImageProcessing", "Python", "Scale"]),
    "0026": ('example of "ImageProcessingUserAPI(Python):ippy.reconstruction"', ["ImageProcessing", "Python", "Reconstruction", "stpy"]),
    "0027": ('example of "HDF5 modification with python"', ["replaces strings in datasets in an HDF5 file", "Python"]),
    "0028": ('example of "UserDatabaseAPI(Python)"', ["Online", "Arrival-timing monitor", "Python"]),
    "0029": ('example of "ImageProcessingUserAPI(Python):ippy.radial_profile"', ["ImageProcessing", "Python", "Radial Profile"]),
    "0030": ('example of "TMAUserAPI(Python)"', ["Online", "Arrival-timing monitor", "Python"]),
    "0032": ('example of "ROI analyzer using olpy, udbpy, dbpy, PyQt"', ["ROI", "olpy", "udbpy", "dbpy", "PyQt"]),
}



def web_examples():
    """{nr: (title, [tags])} from the downloaded Examples page, or the embedded fallback."""
    for page in (WEB / "manuals" / "examples.html", WEB / "manuals" / "examples" / "index.html"):
        t = read(page)
        found = {}
        for nr, block in re.findall(r'<div id="example(\d+)" class="jumpable">(.*?)(?=<div id="example\d+"|<div class="col-sm-3">)', t, re.S):
            title = re.search(r"<p>(.*?)</p>", block, re.S)
            tags = [strip_tags(b) for b in re.findall(r'<span class="badge[^"]*">(.*?)</span>', block, re.S)]
            found[nr] = (strip_tags(title.group(1)).lstrip("#").strip() if title else "", tags)
        if found:
            return found, page.name
    return FALLBACK, "embedded copy"


def read_title_from_readme(text):
    for line in text.splitlines():
        if line.startswith("##") and "keyword" not in line:
            return line.lstrip("#").strip()
    return None


def fence_for(text):
    """Pick a code fence longer than any backtick run in the text."""
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def quote(lines, depth=1):
    prefix = "> " * depth
    return [(prefix + l).rstrip() if l else prefix.rstrip() for l in lines]


def main():
    meta, source = web_examples()
    dirs = {p.name for p in EXAMPLES.iterdir() if p.is_dir() and p.name.isdigit()} if EXAMPLES.is_dir() else set()
    numbers = sorted(set(meta) | dirs)
    texts = {nr: read(EXAMPLES / nr / "readme.txt") for nr in numbers}
    titles = {nr: (meta.get(nr, ("",))[0] or read_title_from_readme(texts[nr]) or "(untitled)") for nr in numbers}

    # Obsidian callouts: "[!type]-" = foldable, collapsed by default.
    # Each callout gets a block ID (^example-NNNN) that the index links to.
    out = header("SACLA Software – Examples", f"{SITE}/manuals/examples", "build_examples.py",
                 back=("Back to SACLA Software", MD / PAGE["software"]), from_file=OUT)
    out += ["> [!abstract]- Index"]
    out += [f"> - [[#^example-{nr}|{nr}]] – {titles[nr]}" for nr in numbers]
    out += ["", "^index", ""]

    missing = []
    for nr in numbers:
        text = texts[nr]
        tags = meta.get(nr, (None, []))[1]
        body = []
        if tags:
            body += [" ".join(f"`{t}`" for t in tags), ""]
        if nr not in meta:
            body += ["*Not listed on the SACLA examples web page.*", ""]
        readme = EXAMPLES / nr / "readme.txt"
        body.append(f"📄 {md_link('readme.txt', rel(readme, OUT))} · [[#^index|↑ Index]]")
        out.append(f"> [!example]- Example {nr} – {titles[nr]}")
        out += quote(body)
        if text.strip():
            fence = fence_for(text)
            code = [f"{fence}text", *text.replace("\r", "").rstrip("\n").split("\n"), fence]
            out += [">", "> > [!note]- Code"]
            out += quote(code, depth=2)
        else:
            missing.append(nr)
            out += quote(["", "⚠️ readme.txt is missing or empty – run `python3 build_all.py --download`."])
        out += ["", f"^example-{nr}", ""]

    write(OUT, "\n".join(out))
    print(f"Wrote {PAGE['examples']} ({len(numbers)} examples, titles from {source})")
    if missing:
        print("  Missing/empty readmes:", ", ".join(missing))


if __name__ == "__main__":
    main()
