"""Render every page in markdown/ to a standalone html page in html/.

The html folder mirrors the markdown folder, so relative links to resources/
work unchanged. On top of a plain conversion it adds:
  - Obsidian callouts  -> collapsible <details> boxes (block IDs become anchors)
  - [[#^id|text]] links -> normal in-page links (opening the target box)
  - .md links          -> .html links
  - .svg diagrams      -> inlined, so every box is clickable and shows a tooltip
  - a navigation bar, light/dark styling, copy buttons on code,
    and search / tag filters on the Examples and Release History pages

Requires:  pip install markdown-it-py
"""
import html
import re
import shutil
from pathlib import Path

from common import HTML, MD, PAGE, read, rel, write

try:
    from markdown_it import MarkdownIt
except ImportError:  # pragma: no cover
    MarkdownIt = None

NAV = [("Software", PAGE["software"]), ("Examples", PAGE["examples"]),
       ("Offline overview", PAGE["overview_offline"]), ("Online overview", PAGE["overview_online"]),
       ("Modules", PAGE["modules"]), ("Release history", PAGE["release"]),
       ("System", PAGE["system"]), ("FAQ", "System/FAQ.md")]

# Pages that get a search box: md name -> (css selector of the filterable items, tag selector)
FILTERS = {
    PAGE["examples"]: ("details.callout-example", ":scope > .callout-body > p:first-child code"),
    PAGE["release"]: ("details.callout-example li, details.callout-example details.callout-note", "code"),
    PAGE["overview_offline"]: ("main li", "code"),
    PAGE["overview_online"]: ("main li", "code"),
    PAGE["software"]: ("main li", None),
}

CALLOUT = re.compile(r"^\[!(\w+)\]([-+]?)\s*(.*)$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
BLOCK_ID = re.compile(r"^\^([A-Za-z0-9-]+)\s*$")


class Renderer:
    def __init__(self):
        self.md = MarkdownIt("commonmark", {"html": True}).enable("table")
        self.stash = []

    # -- obsidian syntax -> html -------------------------------------------------
    def inline(self, text):
        return self.md.renderInline(self.wikilinks(text))

    @staticmethod
    def wikilinks(text):
        text = re.sub(r"\[\[#\^([\w-]+)\|([^\]]+)\]\]", r"[\2](#\1)", text)
        text = re.sub(r"\[\[#\^([\w-]+)\]\]", r"[\1](#\1)", text)
        return text

    def put(self, fragment):
        self.stash.append(fragment)
        return f'<div data-stash="{len(self.stash) - 1}"></div>'

    def blocks(self, lines):
        """Render a list of markdown lines; quote blocks become callouts/blockquotes."""
        out, i, fence = [], 0, None
        while i < len(lines):
            line = lines[i]
            m = FENCE.match(line)
            if fence:
                out.append(line)
                if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                    fence = None
                i += 1
                continue
            if m:
                fence = m.group(1)
                out.append(line)
                i += 1
                continue
            if line.startswith(">"):
                j = i
                while j < len(lines) and lines[j].startswith(">"):
                    j += 1
                inner = [l[2:] if l.startswith("> ") else l[1:] for l in lines[i:j]]
                # a following "^block-id" line names this block
                k = j
                while k < len(lines) and not lines[k].strip():
                    k += 1
                block_id = None
                if k < len(lines) and BLOCK_ID.match(lines[k]):
                    block_id = BLOCK_ID.match(lines[k]).group(1)
                    j = k + 1
                out += ["", self.put(self.quote(inner, block_id)), ""]
                i = j
                continue
            bid = BLOCK_ID.match(line)
            if bid:
                out += ["", self.put(f'<a id="{bid.group(1)}"></a>'), ""]
                i += 1
                continue
            out.append(line)
            i += 1
        return self.render("\n".join(out))

    def quote(self, inner, block_id):
        id_attr = f' id="{block_id}"' if block_id else ""
        m = CALLOUT.match(inner[0]) if inner else None
        if not m:
            return f"<blockquote{id_attr}>{self.blocks(inner)}</blockquote>"
        kind, fold, title = m.group(1).lower(), m.group(2), m.group(3) or m.group(1).capitalize()
        body = self.blocks(inner[1:]) if len(inner) > 1 else ""
        title_html = self.inline(title)
        if fold:
            open_attr = " open" if fold == "+" else ""
            return (f'<details class="callout callout-{kind}"{id_attr}{open_attr}>'
                    f'<summary>{title_html}</summary><div class="callout-body">{body}</div></details>')
        return (f'<div class="callout callout-{kind}"{id_attr}><div class="callout-title">{title_html}</div>'
                f'<div class="callout-body">{body}</div></div>')

    def render(self, text):
        out = self.md.render(self.wikilinks(text))
        # restore stashed html (possibly nested)
        for _ in range(10):
            new = re.sub(r'<div data-stash="(\d+)"></div>', lambda m: self.stash[int(m.group(1))], out)
            if new == out:
                break
            out = new
        return out


# -- page-level post-processing -----------------------------------------------------
def fix_links(body):
    def md_to_html(m):
        attr, url = m.group(1), m.group(2)
        if not re.match(r"[a-z]+:", url) and re.search(r"\.md(#|$)", url):
            url = re.sub(r"\.md(#|$)", r".html\1", url)
        return f'{attr}="{url}"'
    return re.sub(r'(href)="([^"]+)"', md_to_html, body)


def inline_svgs(body, md_file):
    """Replace <img src="x.svg"> of local diagrams with the svg itself (clickable boxes)."""
    def repl(m):
        src = html.unescape(m.group(1))
        svg_file = (md_file.parent / src).resolve()
        svg = read(svg_file)
        if not svg:
            return m.group(0)
        svg = re.sub(r"<\?xml.*?\?>|<!DOCTYPE.*?>", "", svg, flags=re.S)
        svg = re.sub(r'\scontent="[^"]*"', "", svg, count=1)  # drop embedded draw.io source

        def relink(h):
            href = html.unescape(h.group(1))
            if re.match(r"[a-z]+:", href):
                return h.group(0)
            target = (svg_file.parent / href).resolve()
            return f'xlink:href="{html.escape(rel(target, md_file))}" target="_blank"'
        svg = re.sub(r'xlink:href="([^"]+)"(?:\s+target="[^"]*")?', relink, svg)
        return f'<div class="diagram">{svg}</div>'
    return re.sub(r'<img src="([^"]+\.svg)"[^>]*>', repl, body)


def heading_ids(body):
    seen = {}

    def repl(m):
        level, text = m.group(1), m.group(2)
        slug = re.sub(r"[^\w]+", "-", html.unescape(re.sub(r"<[^>]+>", "", text)).lower()).strip("-")
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        slug = f"{slug}-{n}" if n else slug
        return f'<h{level} id="{slug}">{text}</h{level}>'
    return re.sub(r"<h([1-4])>(.*?)</h\1>", repl, body)


def page(title, body, md_name, md_file):
    links = []
    for text, target in NAV:
        cls = ' class="active"' if target == md_name else ""
        links.append(f'<a href="{rel(MD / target, md_file).replace(".md", ".html")}"{cls}>{text}</a>')
    nav = " ".join(links)
    filt = FILTERS.get(md_name)
    data = (f' data-items="{html.escape(filt[0])}"'
            + (f' data-tags="{html.escape(filt[1])}"' if filt[1] else "")) if filt else ""
    css = rel(HTML / "assets" / "style.css", HTML / md_name)
    js = rel(HTML / "assets" / "site.js", HTML / md_name)
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} – SACLA manuals</title>
<link rel="stylesheet" href="{css}">
</head>
<body{data}>
<nav class="topnav">{nav}</nav>
<main>
{body}
</main>
<footer>Generated from <code>markdown/{html.escape(md_name)}</code> by <code>python3 build_all.py</code>.
Source: SACLA HPC portal (RIKEN).</footer>
<script src="{js}"></script>
</body></html>
"""


def main():
    if MarkdownIt is None:
        print("Skipped – install the markdown renderer first:  pip install markdown-it-py")
        return
    if HTML.exists():
        shutil.rmtree(HTML)          # html/ is fully generated
    n = 0
    for md_file in sorted(MD.rglob("*")):
        relpath = md_file.relative_to(MD)
        target = HTML / relpath
        if md_file.is_dir() or any(p.startswith(".") for p in relpath.parts):
            continue
        if md_file.suffix != ".md":
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(md_file, target)          # e.g. Overview/*.svg
            continue
        text = read(md_file)
        title = (re.search(r"^# (.+)$", text, re.M) or [None, md_file.stem])[1]
        body = Renderer().blocks(text.split("\n"))
        body = heading_ids(inline_svgs(fix_links(body), md_file))
        write(target.with_suffix(".html"), page(title, body, relpath.as_posix(), md_file))
        n += 1
    assets = Path(__file__).resolve().parent / "assets"
    shutil.copytree(assets, HTML / "assets")
    start = PAGE["software"].replace(".md", ".html")
    write(HTML / "index.html", f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" '
                               f'content="0; url={start}"><a href="{start}">SACLA manuals</a>')
    print(f"Wrote {n} html pages to html/ (start with html/index.html)")


if __name__ == "__main__":
    main()
