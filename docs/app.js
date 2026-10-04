// Renders data/games.json (made by the builder container) as game cards.
const FILTERS = [
  { id: "news", label: "Latest news", test: g => g.status !== "update" },
  { id: "cracked", label: "Cracked", test: g => ["crack", "denuvo"].includes(g.status) },
  { id: "denuvo", label: "Denuvo cracked", test: g => g.status === "denuvo" },
  { id: "workaround", label: "Workarounds", test: g => g.status === "workaround" },
  { id: "drm", label: "DRM-free / protection removed", test: g => ["gog", "drm_removed"].includes(g.status) },
  { id: "update", label: "Updates only", test: g => g.status === "update" },
  { id: "all", label: "Everything", test: () => true },
];
const TERMS = {
  denuvo: "Denuvo is the strongest anti-piracy protection. Games with it often stay uncracked for months.",
  repack: "A repack is an existing crack, recompressed so it's smaller and easier to install. It doesn't crack anything new.",
  hypervisor: "A hypervisor workaround gets around Denuvo without removing it. You must disable Windows security features, which puts your PC at risk.",
};

let games = [];
let filter = "news";
try { filter = localStorage.getItem("cw-filter") || filter; } catch (e) {}
const $ = id => document.getElementById(id);

function ago(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 3600) return "just now";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  const d = Math.floor(s / 86400);
  return d === 1 ? "yesterday" : d < 31 ? d + " days ago" : new Date(ts * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function card(g) {
  const facts = [
    g.genres.length ? esc(g.genres.join(", ")) : "",
    g.released ? "Out " + esc(g.released) : "",
    g.price ? esc(g.price) + " on Steam" : "",
    g.review ? `${g.review.percent}% positive (${esc(g.review.count)} reviews)` : "",
  ].filter(Boolean).map(f => `<span>${f}</span>`).join("");
  const history = g.timeline.length > 1 ? `
    <details><summary>History (${g.timeline.length})</summary>
      <ul class="timeline">${g.timeline.map(t => `
        <li><div class="t-date">${new Date(t.date * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}
          · <span class="badge s-${t.kind}">${esc(t.label)}</span></div>
          <div>${t.text}${t.size ? ` Size: ${esc(t.size)}.` : ""}</div></li>`).join("")}
      </ul></details>` : "";
  const latest = g.timeline[0];
  return `<article class="card">
    <div class="cover">${g.image ? `<img src="${esc(g.image)}" alt="" loading="lazy">` : `<div class="noimg">${esc(g.name)}</div>`}</div>
    <div class="body">
      <div class="row"><span class="badge s-${g.status}">${esc(g.label)}</span><span class="when">${ago(g.updated)}</span></div>
      <h3>${esc(g.name)}</h3>
      <p class="summary">${g.summary}</p>
      ${facts ? `<div class="facts">${facts}</div>` : ""}
      <div class="links">
        ${g.steam_url ? `<a href="${esc(g.steam_url)}" target="_blank" rel="noopener">Steam page</a>` : ""}
        <a href="${esc(latest.post)}" target="_blank" rel="noopener">Original post</a>
      </div>
      ${history}
    </div></article>`;
}

function render() {
  const q = $("q").value.trim().toLowerCase();
  const f = FILTERS.find(x => x.id === filter) || FILTERS[0];
  $("chips").innerHTML = FILTERS.map(x => `<button class="chip" role="tab" data-f="${x.id}" aria-selected="${x.id === f.id}">
    ${x.label}<span class="n">${games.filter(x.test).length}</span></button>`).join("");
  const shown = games.filter(g => f.test(g) && (!q || g.name.toLowerCase().includes(q)));
  $("grid").innerHTML = shown.slice(0, 300).map(card).join("");
  $("empty").hidden = shown.length > 0;
}

$("chips").addEventListener("click", e => {
  const b = e.target.closest("[data-f]");
  if (!b) return;
  filter = b.dataset.f;
  try { localStorage.setItem("cw-filter", filter); } catch (e) {}
  render();
});
$("q").addEventListener("input", render);

// Glossary pop-ups on dashed words.
const tip = $("tip");
document.addEventListener("mouseover", e => {
  const t = e.target.closest(".term");
  if (!t || !TERMS[t.dataset.t]) return;
  tip.textContent = TERMS[t.dataset.t];
  tip.hidden = false;
  const r = t.getBoundingClientRect();
  tip.style.left = Math.min(r.left, innerWidth - 296) + "px";
  tip.style.top = (r.bottom + 8) + "px";
});
document.addEventListener("mouseout", e => { if (e.target.closest(".term")) tip.hidden = true; });
document.addEventListener("click", e => { if (e.target.closest(".term")) location.hash = "glossary"; });

// On GitHub Pages (user.github.io/repo/) point the contact link at the repo's issues.
const gh = location.hostname.match(/^([\w-]+)\.github\.io$/);
if (gh) {
  const repo = location.pathname.split("/")[1] || `${gh[1]}.github.io`;
  $("issues").href = `https://github.com/${gh[1]}/${repo}/issues`;
  $("contact").hidden = false;
}

fetch("data/games.json", { cache: "no-store" })
  .then(r => r.json())
  .then(d => {
    games = d.games;
    $("meta").textContent = `${games.length} games · updated ${ago(d.built)}`;
    render();
  })
  .catch(() => {
    // Opened as a file or from an editor preview instead of through the web container.
    $("meta").innerHTML = location.protocol === "file:"
      ? 'This page only works through a web server: open <a href="http://localhost:8080">http://localhost:8080</a>.'
      : "No data yet. Check back in a few minutes.";
  });
