// Offline search over window.SEARCH_INDEX (written by build_docs.py) + sidebar highlight.
(function () {
  "use strict";
  var current = document.body.getAttribute("data-page");
  document.querySelectorAll("nav a").forEach(function (a) {
    if (a.getAttribute("href") === current) { a.classList.add("current"); }
  });

  var input = document.getElementById("search");
  var list = document.getElementById("results");
  var index = window.SEARCH_INDEX || [];
  var selected = -1;

  function escapeHtml(s) {
    return s.replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function snippet(text, word) {
    var i = text.toLowerCase().indexOf(word);
    if (i < 0) { return text.slice(0, 120); }
    var start = Math.max(0, i - 50);
    return (start > 0 ? "…" : "") + text.slice(start, i + 90) + "…";
  }

  function search(q) {
    var words = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) { return []; }
    var hits = [];
    index.forEach(function (p) {
      var title = p.title.toLowerCase(), text = p.text.toLowerCase(), score = 0;
      for (var i = 0; i < words.length; i++) {
        var w = words[i];
        if (title.indexOf(w) < 0 && text.indexOf(w) < 0) { return; }
        if (title.indexOf(w) >= 0) { score += 10; }
        score += Math.min(text.split(w).length - 1, 10);
      }
      hits.push({ page: p, score: score });
    });
    hits.sort(function (a, b) { return b.score - a.score; });
    return hits.slice(0, 12).map(function (h) {
      return { url: h.page.url, title: h.page.title, snip: snippet(h.page.text, words[0]) };
    });
  }

  function render(hits) {
    selected = -1;
    list.innerHTML = hits.map(function (h) {
      return '<li><a href="' + h.url + '"><span class="t">' + escapeHtml(h.title) +
        '</span><span class="s">' + escapeHtml(h.snip) + "</span></a></li>";
    }).join("");
  }

  input.addEventListener("input", function () { render(search(input.value)); });
  input.addEventListener("keydown", function (e) {
    var links = list.querySelectorAll("a");
    if (!links.length) { return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      selected = (selected + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
      links.forEach(function (a, i) { a.classList.toggle("sel", i === selected); });
    } else if (e.key === "Enter") {
      window.location.href = links[Math.max(selected, 0)].getAttribute("href");
    } else if (e.key === "Escape") {
      list.innerHTML = ""; input.blur();
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && document.activeElement !== input) { e.preventDefault(); input.focus(); }
  });
  document.addEventListener("click", function (e) {
    if (!e.target.closest(".search")) { list.innerHTML = ""; }
  });
})();
