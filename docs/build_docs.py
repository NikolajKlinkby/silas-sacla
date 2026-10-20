#!/usr/bin/env python3
"""Build the SILAS documentation (a self-contained HTML wiki).

    pip install markdown markdown-it-py   # once, in the Python you build with (3.8+ for the manuals)
    python docs/build_docs.py             # -> docs/html/index.html, and the SACLA manuals
    python docs/build_docs.py --no-manuals   # without rebuilding the SACLA manuals

Sources
  docs/src/*.md        hand-written guide pages (order, titles, sidebar categories: CATEGORIES below)
  silas/**/*.py     reference pages, generated from the docstrings
  silas/cli/*.py    the list of session commands, generated from the code
  docs/sacla_manuals/  the local copy of SACLA's manuals: rebuilt by its own build_all.py and
                       linked from the sidebar (docs/sacla_manuals/html/)

The code is read with ``ast`` and never imported, so building the docs does not
run the bootstrap, create config files or need any SACLA library.
Output is flat (all pages in docs/html/) and works offline from the file system.
"""
import ast
import datetime
import html
import json
import os
import re
import shutil
import sys
from typing import Dict, List, Optional, Tuple

try:
    import markdown
except ImportError:
    sys.exit("The 'markdown' package is needed to build the docs: pip install markdown")

DOCS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DOCS)
SRC = os.path.join(DOCS, "src")
OUT = os.path.join(DOCS, "html")
PACKAGE = os.path.join(ROOT, "silas")

# Sidebar categories: (category, [(markdown file, title in the sidebar), ...]). Each category is a
# collapsible section, open by default. The first page becomes index.html.
CATEGORIES = [
    ("Start here", [
        ("index.md", "Home"),
        ("sacla-basics.md", "SACLA basics"),
        ("getting-started.md", "Getting started"),
        ("setup-guide.md", "Setting up a beamtime, step by step"),
    ]),
    ("Where it runs", [
        ("beamline.md", "Working at the beamline"),
        ("hpc.md", "Working on the HPC"),
        ("jupyter.md", "Jupyter notebooks on the HPC"),
        ("profiles-and-modes.md", "Profiles and modes"),
    ]),
    ("Setup and calibration", [
        ("configuration.md", "Configuration"),
        ("detectors.md", "Detectors and aliases"),
        ("run-logger.md", "Run logger"),
        ("timetool.md", "Time tool"),
        ("mpccd-viewer.md", "MPCCD viewer"),
        ("energy-calibration.md", "Energy calibration, threshold and dark"),
        ("rois.md", "Regions of interest (ROIs)"),
    ]),
    ("Analysis", [
        ("scans.md", "Scan analysis"),
        ("fullframe.md", "Full-frame reduction"),
    ]),
    ("Development", [
        ("scripts.md", "Running and writing scripts"),
        ("development.md", "Development notes"),
    ]),
]  # type: List[Tuple[str, List[Tuple[str, str]]]]
GUIDE = [page_ for _, pages in CATEGORIES for page_ in pages]

HAS_MANUALS = False   # set by main(): the SACLA manuals' html exists (sidebar link)

MD_EXTENSIONS = ["fenced_code", "tables", "toc", "sane_lists", "attr_list"]


# ====================================================================== page template
def page(title: str, body: str, nav: str, current: str) -> str:
    built = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} – SILAS</title>
<link rel="stylesheet" href="style.css">
</head>
<body data-page="{current}">
<header>
  <a class="brand" href="index.html">SILAS <span>docs</span></a>
  <div class="search">
    <input id="search" type="search" placeholder="Search the docs…" autocomplete="off">
    <ul id="results"></ul>
  </div>
</header>
<div class="layout">
<nav>{nav}</nav>
<main>
{body}
<footer>Built {built} from the SILAS sources · <code>python docs/build_docs.py</code></footer>
</main>
</div>
<script src="search_index.js"></script>
<script src="search.js"></script>
</body>
</html>
""".format(title=html.escape(title), body=body, nav=nav, current=current, built=built)


def nav_html(ref_pages: List[Tuple[str, str]]) -> str:
    """The sidebar: one collapsible section (open) per category, then the reference."""
    parts = []
    for category, pages in CATEGORIES:
        items = "".join('<li><a href="{}">{}</a></li>'.format(html_name(md), html.escape(t)) for md, t in pages)
        parts.append("<details open><summary>{}</summary><ul>{}</ul></details>".format(html.escape(category), items))
    ref = '<li><a href="commands.html">Session commands</a></li>'
    if HAS_MANUALS:
        ref += '<li><a href="{}">SACLA manuals (local copy)</a></li>'.format(MANUALS_INDEX)
    ref += "".join('<li><a href="{}"><code>{}</code></a></li>'.format(f, html.escape(m)) for f, m in ref_pages)
    parts.append("<details open><summary>Reference</summary><ul>{}</ul></details>".format(ref))
    return "".join(parts)


def html_name(md_file: str) -> str:
    return "index.html" if md_file == GUIDE[0][0] else md_file[:-3] + ".html"


# ====================================================================== guide pages
def fix_links(body: str) -> str:
    """Links to other .md pages -> .html (also with #anchors)."""
    def repl(m: "re.Match[str]") -> str:
        target, anchor = m.group(1), m.group(2) or ""
        return 'href="{}{}"'.format(html_name(target), anchor)
    return re.sub(r'href="([\w\-]+\.md)(#[\w\-]*)?"', repl, body)


def build_guide(nav: str, index: List[Dict[str, str]]) -> None:
    for md_file, title in GUIDE:
        path = os.path.join(SRC, md_file)
        if not os.path.exists(path):
            print("  missing guide page: docs/src/{} (skipped)".format(md_file))
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        body = fix_links(markdown.markdown(text, extensions=MD_EXTENSIONS))
        out = html_name(md_file)
        write(out, page(title, body, nav, out))
        index.append({"title": title, "url": out, "text": strip_tags(body)})


# ====================================================================== docstrings -> html
def doc_html(doc: Optional[str]) -> str:
    """Docstring as HTML: paragraphs, indented blocks as code, ``x`` as code."""
    if not doc:
        return ""
    lines = doc.expandtabs().split("\n")
    # dedent everything after the first line (PEP 257)
    indent = min((len(l) - len(l.lstrip()) for l in lines[1:] if l.strip()), default=0)
    lines = [lines[0].strip()] + [l[indent:] for l in lines[1:]]
    blocks, cur, cur_code = [], [], False  # type: List[Tuple[bool, List[str]]], List[str], bool
    for line in lines + [""]:
        # indented lines continue a list / definition block instead of starting code
        structured = bool(cur) and not cur_code and any(_is_item(l) or _is_def(l) for l in cur)
        is_code = line.startswith("    ") and not structured
        if not line.strip():
            if cur:
                blocks.append((cur_code, cur))
                cur = []
            continue
        if cur and is_code != cur_code:
            blocks.append((cur_code, cur))
            cur = []
        cur_code = is_code
        cur.append(line)
    # merge code blocks separated only by blank lines
    merged = []  # type: List[Tuple[bool, List[str]]]
    for code, blk in blocks:
        if code and merged and merged[-1][0]:
            merged[-1] = (True, merged[-1][1] + [""] + blk)
        else:
            merged.append((code, blk))
    out = []
    for code, blk in merged:
        if code:
            text = "\n".join(l[4:] if l.startswith("    ") else l for l in blk)
            out.append("<pre><code>{}</code></pre>".format(html.escape(text)))
        else:
            text = html.escape(" ".join(l.strip() for l in blk)).rstrip(":")
            text = re.sub(r"``(.+?)``", r"<code>\1</code>", text)
            # "name   explanation" lists (two+ spaces) keep their line breaks
            if any(_is_item(l) for l in blk):
                out.append(_bullets(blk))
            elif any(_is_def(l) for l in blk) and len(blk) > 1:
                rows = []
                for l in blk:
                    m = re.match(r"^\s*(\S+(?: \S+)*?)\s{2,}(.*)$", l)
                    if m:
                        rows.append("<tr><td><code>{}</code></td><td>{}</td></tr>".format(
                            html.escape(m.group(1)), re.sub(r"``(.+?)``", r"<code>\1</code>", html.escape(m.group(2)))))
                    elif rows:
                        rows[-1] = rows[-1][:-len("</td></tr>")] + " " + _inline(l.strip()) + "</td></tr>"
                    else:
                        rows.append('<tr><td colspan="2">{}</td></tr>'.format(_inline(l.strip())))
                out.append('<table class="deflist">{}</table>'.format("".join(rows)))
            elif any(_is_item(l) for l in blk):
                out.append(_bullets(blk))
            else:
                out.append("<p>{}</p>".format(text))
    return "\n".join(out)


_ITEM = re.compile(r"^\s*(?:[*-]|\d+\.)\s+")


def _is_item(line: str) -> bool:
    """Bullet ('* x', '- x') or numbered ('1. x') list item."""
    return bool(_ITEM.match(line))


def _is_def(line: str) -> bool:
    """'name   explanation' (two or more spaces between term and text)."""
    return bool(re.match(r"^\s*\S+(?: \S+)*?\s{2,}\S", line))


def _inline(text: str) -> str:
    return re.sub(r"``(.+?)``", r"<code>\1</code>", html.escape(text))


def _bullets(blk: List[str]) -> str:
    """Lines before the first bullet -> paragraph, bullets (with continuation lines) -> list."""
    intro, items = [], []  # type: List[str], List[str]
    numbered = False
    for l in blk:
        t = l.strip()
        if _is_item(t):
            numbered = numbered or t[0].isdigit()
            items.append(_ITEM.sub("", t))
        elif items:
            items[-1] += " " + t
        else:
            intro.append(t)
    head = "<p>{}</p>".format(_inline(" ".join(intro))) if intro else ""
    tag = "ol" if numbered else "ul"
    return head + "<{0}>{1}</{0}>".format(tag, "".join("<li>{}</li>".format(_inline(i)) for i in items))


def signature(fn: ast.FunctionDef, drop_first: bool = False) -> str:
    args = fn.args
    if drop_first:  # bound commands: the cfg argument is filled in by the session
        rest = args.args[1:]
        defaults = args.defaults[len(args.defaults) - len(rest):] if len(args.defaults) > len(rest) else args.defaults
        args = ast.arguments(posonlyargs=[], args=rest, vararg=args.vararg, kwonlyargs=args.kwonlyargs,
                             kw_defaults=args.kw_defaults, kwarg=args.kwarg, defaults=defaults)
    sig = ast.unparse(args)
    ret = " -> " + ast.unparse(fn.returns) if fn.returns is not None else ""
    return "({}){}".format(sig, ret)


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


# ====================================================================== reference pages
def module_name(path: str) -> str:
    rel = os.path.relpath(path, ROOT)[:-3].replace(os.sep, ".")
    return rel[:-9] if rel.endswith(".__init__") else rel


def parse(path: str) -> ast.Module:
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read(), path)


def function_html(fn: ast.FunctionDef, prefix: str = "", drop_first: bool = False,
                  shown_name: Optional[str] = None) -> str:
    name = shown_name or fn.name
    deco = "".join('<span class="deco">@{}</span> '.format(html.escape(ast.unparse(d))) for d in fn.decorator_list)
    return ('<div class="member" id="{anchor}"><div class="sig">{deco}<b>{prefix}{name}</b>'
            '<span class="args">{sig}</span></div>{doc}</div>').format(
        anchor=html.escape(prefix + name), deco=deco, prefix=html.escape(prefix), name=html.escape(name),
        sig=html.escape(signature(fn, drop_first)), doc=doc_html(ast.get_docstring(fn)))


def module_page(path: str) -> Tuple[str, str, str]:
    """(module name, html body, plain text) of one module."""
    tree = parse(path)
    name = module_name(path)
    parts = ["<h1><code>{}</code></h1>".format(html.escape(name)),
             '<p class="src">Source: <code>{}</code></p>'.format(html.escape(os.path.relpath(path, ROOT))),
             doc_html(ast.get_docstring(tree))]
    consts, funcs, classes = [], [], []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            funcs.append(function_html(node))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods = [function_html(m, prefix="") for m in node.body
                       if isinstance(m, ast.FunctionDef) and (not m.name.startswith("_") or m.name == "__init__")]
            classes.append('<div class="member cls" id="{0}"><div class="sig"><b>class {0}</b></div>{1}'
                           '<div class="methods">{2}</div></div>'.format(
                               html.escape(node.name), doc_html(ast.get_docstring(node)), "".join(methods)))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            cname = node.targets[0].id
            if cname.isupper() and not cname.startswith("_"):
                value = ast.unparse(node.value)
                if len(value) > 300:
                    value = value[:300] + " …"
                consts.append("<tr><td><code>{}</code></td><td><code>{}</code></td></tr>".format(
                    html.escape(cname), html.escape(value)))
    if consts:
        parts.append('<h2>Constants</h2><table class="consts">{}</table>'.format("".join(consts)))
    if classes:
        parts.append("<h2>Classes</h2>" + "".join(classes))
    if funcs:
        parts.append("<h2>Functions</h2>" + "".join(funcs))
    body = "\n".join(parts)
    return name, body, strip_tags(body)


def reference_files() -> List[str]:
    files = []
    for folder, dirs, names in os.walk(PACKAGE):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "__")))
        for n in sorted(names):
            if n.endswith(".py"):
                files.append(os.path.join(folder, n))
    return sorted(files, key=module_name)


# ====================================================================== session commands
def _imports(tree: ast.Module) -> Dict[str, str]:
    """{local name: module file} for `from silas.x import a, b` and `import silas.x as y`."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("silas"):
            for a in node.names:
                local = a.asname or a.name
                cand = os.path.join(ROOT, *(node.module.split(".") + [a.name])) + ".py"
                if os.path.exists(cand):
                    out[local] = cand
    return out


def _find_def(path: str, name: str) -> Optional[ast.FunctionDef]:
    for node in parse(path).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _nested_defs(fn: ast.FunctionDef) -> Dict[str, ast.FunctionDef]:
    return {n.name: n for n in ast.walk(fn) if isinstance(n, ast.FunctionDef) and n is not fn}


NAMESPACE_VARS = ("ns", "cmds")  # variables holding the session namespace in shell.py / commands.py

SPECIAL = {
    "cfg": "The configuration of the active profile (a <code>Config</code> object): "
           "<code>cfg.show()</code>, <code>cfg.get(key)</code>, <code>cfg.set(key, value)</code>. "
           'See <a href="configuration.html">Configuration</a>.',
    "silas": "The silas package itself.",
}


def command_entries() -> List[Tuple[str, str]]:
    """[(name, html)] of everything in the session namespace."""
    entries = []  # type: List[Tuple[str, str]]
    for rel, builder in (("silas/cli/shell.py", "build_namespace"), ("silas/cli/commands.py", "build")):
        path = os.path.join(ROOT, rel)
        tree = parse(path)
        imports = _imports(tree)
        build = _find_def(path, builder)
        if build is None:
            continue
        nested = _nested_defs(build)
        for node in build.body:  # only top-level statements of build(): `ns = {...}`, `ns["x"] = ...`
            pairs = []  # type: List[Tuple[str, ast.expr]]
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if (isinstance(target, ast.Name) and target.id in NAMESPACE_VARS
                    and isinstance(node.value, ast.Dict)):
                pairs = [(k.value, v) for k, v in zip(node.value.keys, node.value.values)
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            elif (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                  and target.value.id in NAMESPACE_VARS and isinstance(target.slice, ast.Constant)):
                pairs = [(target.slice.value, node.value)]
            for key, value in pairs:
                entries.append((key, _command_html(key, value, nested, imports, rel)))
    seen, out = set(), []
    for k, h in entries:
        if k not in seen:
            seen.add(k)
            out.append((k, h))
    return sorted(out)


def _command_html(key: str, value: ast.expr, nested: Dict[str, ast.FunctionDef],
                  imports: Dict[str, str], rel: str) -> str:
    if key in SPECIAL:
        return '<div class="member" id="{0}"><div class="sig"><b>{0}</b></div><p>{1}</p></div>'.format(key, SPECIAL[key])
    drop_first = False
    if isinstance(value, ast.Call) and ast.unparse(value.func) == "_bind":
        value, drop_first = value.args[1], True
    fn = None  # type: Optional[ast.FunctionDef]
    where = rel
    if isinstance(value, ast.Name) and value.id in nested:
        fn = nested[value.id]
    elif isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id in imports:
        fn = _find_def(imports[value.value.id], value.attr)
        where = os.path.relpath(imports[value.value.id], ROOT)
    if fn is None:
        return '<div class="member" id="{0}"><div class="sig"><b>{0}</b></div></div>'.format(html.escape(key))
    body = function_html(fn, drop_first=drop_first, shown_name=key)
    note = '<p class="src">defined in <code>{}</code></p></div>'.format(html.escape(where))
    return body[:-len("</div>")] + note


def commands_page() -> Tuple[str, str]:
    entries = command_entries()
    smoke = [k for k, h in entries if "@sacla.requires_sacla" in h]
    body = ["<h1>Session commands</h1>",
            "<p>Everything available at the prompt of the SILAS session "
            "(<code>python start_session.py</code>). In the session, <code>commands()</code> prints this list "
            "and <code>help(name)</code> shows the full text of one command.</p>",
            "<p>Commands defined in <code>silas.core.names</code> take the configuration as their first "
            "argument in Python code; in the session it is filled in for you, so it is not shown here.</p>",
            "<p>Commands marked <span class=\"deco\">@sacla.requires_sacla</span> talk to a SACLA library. "
            "In <b>smoke</b> mode they are not executed: their source file is checked with "
            "<code>sacla_check</code> instead (see <a href=\"profiles-and-modes.html\">Profiles and modes</a>). "
            "These are: {}.</p>".format(", ".join("<code>{}</code>".format(k) for k in smoke) or "none"),
            '<div class="toc">{}</div>'.format(" · ".join('<a href="#{0}">{0}</a>'.format(k) for k, _ in entries))]
    body += [h for _, h in entries]
    text = "\n".join(body)
    return text, strip_tags(text)


# ====================================================================== output
def write(name: str, content: str) -> None:
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
        fh.write(content)


MANUALS = os.path.join(DOCS, "sacla_manuals")            # local copy of the SACLA manuals (own builder)
MANUALS_INDEX = "../sacla_manuals/html/SACLA_Software.html"  # from docs/html/


def build_manuals() -> bool:
    """Rebuild the SACLA manuals (docs/sacla_manuals/build_all.py: markdown + html from the downloaded
    files, no download). True if their html is there to link to."""
    import subprocess

    builder = os.path.join(MANUALS, "build_all.py")
    if not os.path.exists(builder):
        print("  no docs/sacla_manuals: the SACLA manuals are not linked")
        return False
    if not os.path.isdir(os.path.join(MANUALS, "resources")):
        print("  docs/sacla_manuals has no resources/ (downloaded manuals): python docs/sacla_manuals/build_all.py "
              "--download on the VPN")
    print("SACLA manuals: python docs/sacla_manuals/build_all.py")
    rc = subprocess.call([sys.executable, builder], cwd=MANUALS)
    if rc != 0:
        print("  the SACLA manuals builder failed (exit {}); the old pages are linked if there".format(rc))
    return os.path.exists(os.path.join(MANUALS, "html", "SACLA_Software.html"))


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="build the SILAS docs (docs/html) and the SACLA manuals")
    ap.add_argument("--no-manuals", action="store_true", help="do not rebuild the SACLA manuals (still linked if built)")
    args = ap.parse_args()
    global HAS_MANUALS
    if args.no_manuals:
        HAS_MANUALS = os.path.exists(os.path.join(MANUALS, "html", "SACLA_Software.html"))
    else:
        HAS_MANUALS = build_manuals()
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    for asset in ("style.css", "search.js"):
        shutil.copy(os.path.join(DOCS, "assets", asset), os.path.join(OUT, asset))
    images = os.path.join(SRC, "img")          # pictures of the guide pages: ![...](img/name.svg)
    if os.path.isdir(images):
        shutil.copytree(images, os.path.join(OUT, "img"))

    ref_pages = []  # type: List[Tuple[str, str, str, str]]
    for path in reference_files():
        name, body, text = module_page(path)
        ref_pages.append(("ref-{}.html".format(name), name, body, text))
    nav = nav_html([(f, n) for f, n, _, _ in ref_pages])

    index = []  # type: List[Dict[str, str]]
    build_guide(nav, index)
    body, text = commands_page()
    write("commands.html", page("Session commands", body, nav, "commands.html"))
    index.append({"title": "Session commands", "url": "commands.html", "text": text})
    for fname, name, body, text in ref_pages:
        write(fname, page(name, body, nav, fname))
        index.append({"title": name, "url": fname, "text": text})

    write("search_index.js", "window.SEARCH_INDEX = {};\n".format(json.dumps(index)))
    print("docs built: {} pages -> {}".format(len(index), os.path.join(OUT, "index.html")))


if __name__ == "__main__":
    main()
