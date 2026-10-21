// Search, sound playback and list filters for the PureZ wiki.
(() => {
  // the site may be served from a subpath (github.io/<repo>) as well as from its own domain
  const base = new URL(document.currentScript.src).pathname.replace(/\/static\/wiki\.js$/, "");
  const q = document.getElementById("q");
  const results = document.getElementById("results");
  let index = null, sel = -1;

  const load = async () => {
    if (index) return index;
    try { index = await (await fetch(`${base}/static/search.json`)).json(); } catch { index = []; }
    return index;
  };

  const score = (e, terms) => {
    const t = e.t.toLowerCase(), c = e.c.toLowerCase();
    let s = 0;
    for (const w of terms) {
      if (t.startsWith(w) || c.startsWith(w)) s += 3;
      else if (t.includes(w) || c.includes(w)) s += 1;
      else return 0;
    }
    return s - t.length / 200;
  };

  const render = async () => {
    const terms = q.value.toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) { results.hidden = true; return; }
    const hits = (await load()).map((e) => [score(e, terms), e]).filter(([s]) => s > 0)
      .sort((a, b) => b[0] - a[0]).slice(0, 12);
    sel = hits.length ? 0 : -1;
    results.replaceChildren(...hits.map(([, e], i) => {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", i === sel);
      const a = document.createElement("a");
      a.href = base + e.u;
      a.append(document.createTextNode(e.t));
      const small = document.createElement("small");
      small.textContent = e.k;
      a.append(small);
      li.append(a);
      return li;
    }));
    if (!hits.length) {
      const li = document.createElement("li");
      li.innerHTML = '<a aria-disabled="true">No matches</a>';
      results.append(li);
    }
    results.hidden = false;
  };

  if (q) {
    q.addEventListener("input", render);
    q.addEventListener("focus", () => { load(); if (q.value) render(); });
    q.addEventListener("keydown", (e) => {
      const items = [...results.querySelectorAll("li[role=option]")];
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!items.length) return;
        sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items.forEach((li, i) => li.setAttribute("aria-selected", i === sel));
      } else if (e.key === "Enter" && items[sel]) {
        location.href = items[sel].querySelector("a").href;
      } else if (e.key === "Escape") {
        q.blur(); results.hidden = true;
      }
    });
    document.addEventListener("click", (e) => { if (!e.target.closest(".search")) results.hidden = true; });
    document.addEventListener("keydown", (e) => {
      if (e.key === "/" && document.activeElement !== q && !/input|textarea/i.test(document.activeElement.tagName)) {
        e.preventDefault(); q.focus();
      }
    });
    document.querySelectorAll("[data-focus-search]").forEach((b) => b.addEventListener("click", () => q.focus()));
  }

  // Sound buttons: one clip at a time
  let audio = null, playing = null;
  document.querySelectorAll(".play[data-src]").forEach((b) => b.addEventListener("click", () => {
    if (audio) { audio.pause(); playing?.setAttribute("aria-pressed", "false"); }
    if (playing === b) { playing = null; return; }
    audio = new Audio(b.dataset.src);
    playing = b;
    b.setAttribute("aria-pressed", "true");
    audio.onended = () => b.setAttribute("aria-pressed", "false");
    audio.play();
  }));

  // Collapsed sections stay collapsed on the next page (per heading)
  let closed = {};
  try { closed = JSON.parse(localStorage.getItem("pz-wiki-closed") || "{}"); } catch {}
  document.querySelectorAll("details.block, details.group").forEach((d) => {
    const key = (d.querySelector("summary h2") || {}).textContent;
    if (!key) return;
    if (closed[key]) d.open = false;
    d.addEventListener("toggle", () => {
      if (d.open) delete closed[key]; else closed[key] = 1;
      try { localStorage.setItem("pz-wiki-closed", JSON.stringify(closed)); } catch {}
    });
  });
  // a group opened by its anchor is always shown
  if (location.hash) { const t = document.querySelector(location.hash); if (t && t.tagName === "DETAILS") t.open = true; }

  // Render views: thumbnails swap the main image, or bring the 3D model back
  document.querySelectorAll(".media .views").forEach((strip) => {
    const fig = strip.closest(".media");
    const viewer = fig.querySelector("model-viewer");
    let img = fig.querySelector(":scope > img");
    strip.querySelectorAll("button[data-view]").forEach((b) => b.addEventListener("click", () => {
      strip.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
      if (b.dataset.view === "model") { if (viewer) viewer.hidden = false; if (img) img.hidden = true; return; }
      if (!img) { img = document.createElement("img"); img.alt = ""; fig.prepend(img); }
      img.src = b.dataset.view; img.hidden = false;
      if (viewer) viewer.hidden = true;
    }));
  });

  // Images: tiles or list, remembered
  document.querySelectorAll(".gallery-bar [data-gallery]").forEach((b) => b.addEventListener("click", () => {
    const g = b.closest("details").querySelector(".gallery");
    g.classList.toggle("tiles", b.dataset.gallery === "tiles"); g.classList.toggle("list", b.dataset.gallery === "list");
    b.parentElement.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    try { localStorage.setItem("pz-gallery", b.dataset.gallery); } catch {}
  }));
  try { const m = localStorage.getItem("pz-gallery"); if (m) document.querySelectorAll(`.gallery-bar [data-gallery="${m}"]`).forEach((b) => b.click()); } catch {}

  // List filters
  document.querySelectorAll("input[data-filter]").forEach((f) => {
    const rows = [...document.querySelectorAll(f.dataset.filter)];
    f.addEventListener("input", () => {
      const w = f.value.toLowerCase().trim();
      rows.forEach((r) => { r.hidden = w && !r.dataset.text.includes(w); });
    });
  });
})();
