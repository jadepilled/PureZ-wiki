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

  // List filters
  document.querySelectorAll("input[data-filter]").forEach((f) => {
    const rows = [...document.querySelectorAll(f.dataset.filter)];
    f.addEventListener("input", () => {
      const w = f.value.toLowerCase().trim();
      rows.forEach((r) => { r.hidden = w && !r.dataset.text.includes(w); });
    });
  });
})();
