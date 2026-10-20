// Small enhancements for the generated SACLA manual pages (no dependencies).
(function () {
  "use strict";

  // ---- copy buttons on code blocks
  document.querySelectorAll("pre > code").forEach(function (code) {
    var btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.type = "button";
    btn.textContent = "Copy";
    btn.addEventListener("click", function () {
      var done = function () { btn.textContent = "Copied"; setTimeout(function () { btn.textContent = "Copy"; }, 1200); };
      if (navigator.clipboard) navigator.clipboard.writeText(code.innerText).then(done, function () {});
    });
    code.parentElement.appendChild(btn);
  });

  // ---- in-page links open the collapsed box they point to
  function openTarget(id) {
    var el = id && document.getElementById(id);
    if (!el) return;
    for (var p = el; p; p = p.parentElement) if (p.tagName === "DETAILS") p.open = true;
    el.scrollIntoView({ block: "start" });
    el.classList.add("flash");
    setTimeout(function () { el.classList.remove("flash"); }, 1500);
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    var id = decodeURIComponent(a.getAttribute("href").slice(1));
    if (document.getElementById(id)) { e.preventDefault(); history.replaceState(null, "", "#" + id); openTarget(id); }
  });
  if (location.hash) openTarget(decodeURIComponent(location.hash.slice(1)));

  // ---- search + tag filter (pages with data-items)
  var itemSel = document.body.dataset.items;
  if (!itemSel) return;
  var tagSel = document.body.dataset.tags;
  var main = document.querySelector("main");
  var items = Array.prototype.slice.call(main.querySelectorAll(itemSel));
  if (!items.length) return;
  var boxes = Array.prototype.slice.call(main.querySelectorAll("details"));

  var tagsOf = new Map(), counts = {};
  items.forEach(function (it) {
    var tags = [];
    if (tagSel) {
      try { it.querySelectorAll(tagSel).forEach(function (c) { tags.push(c.textContent.trim()); }); } catch (err) {}
    }
    tags = Array.from(new Set(tags));
    tagsOf.set(it, tags);
    tags.forEach(function (t) { counts[t] = (counts[t] || 0) + 1; });
  });

  var bar = document.createElement("div");
  bar.className = "toolbar";
  bar.innerHTML =
    '<div class="toolbar-row"><input type="search" placeholder="Search this page…" aria-label="Search">' +
    '<button type="button" data-act="open">Expand all</button>' +
    '<button type="button" data-act="close">Collapse all</button>' +
    '<span class="count"></span></div><div class="chips"></div>';
  var h1 = main.querySelector("h1");
  var anchor = h1 ? h1.nextElementSibling : main.firstChild;
  while (anchor && anchor.tagName === "P") anchor = anchor.nextElementSibling;
  main.insertBefore(bar, anchor);

  var input = bar.querySelector("input"), countEl = bar.querySelector(".count"), chipsEl = bar.querySelector(".chips");
  var active = new Set();
  Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a] || a.localeCompare(b); })
    .forEach(function (t) {
      var c = document.createElement("button");
      c.type = "button"; c.className = "chip";
      c.innerHTML = t.replace(/[&<>]/g, function (ch) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]; }) +
        " <small>" + counts[t] + "</small>";
      c.addEventListener("click", function () {
        if (active.has(t)) active.delete(t); else active.add(t);
        c.classList.toggle("on");
        apply();
      });
      chipsEl.appendChild(c);
    });
  if (!chipsEl.children.length) chipsEl.remove();

  function apply() {
    var q = input.value.trim().toLowerCase(), filtering = q || active.size, shown = 0;
    items.forEach(function (it) {
      var ok = true;
      if (q) ok = it.textContent.toLowerCase().indexOf(q) >= 0;
      if (ok && active.size) { var tg = tagsOf.get(it); active.forEach(function (t) { if (tg.indexOf(t) < 0) ok = false; }); }
      it.hidden = !ok;
      if (ok) shown++;
    });
    // boxes that only hold filtered items: hide when empty, open when they contain hits
    boxes.forEach(function (d) {
      if (items.indexOf(d) >= 0) return;
      var inner = items.filter(function (it) { return d.contains(it); });
      if (!inner.length) return;
      var any = inner.some(function (it) { return !it.hidden; });
      d.hidden = filtering && !any;
      if (filtering) d.open = any; else d.open = false;
    });
    // headings/lists whose items are all hidden
    main.querySelectorAll("ul").forEach(function (ul) {
      var lis = Array.prototype.filter.call(ul.children, function (li) { return items.indexOf(li) >= 0; });
      if (!lis.length) return;
      var any = lis.some(function (li) { return !li.hidden; });
      ul.hidden = !any;
      var h = ul.previousElementSibling;
      if (h && /^H[2-4]$/.test(h.tagName)) h.hidden = !any;
    });
    countEl.textContent = filtering ? shown + " of " + items.length + " shown" : items.length + " items";
  }
  input.addEventListener("input", apply);
  bar.addEventListener("click", function (e) {
    var act = e.target.dataset && e.target.dataset.act;
    if (!act) return;
    boxes.forEach(function (d) { if (!d.hidden) d.open = act === "open"; });
  });
  apply();
})();
