// Renders data/games.json (recent) as game cards; data/archive.json (older) loads on demand.
const FILTERS = [
  { id: "news", label: "Latest news", test: g => g.status !== "update" },
  { id: "cracked", label: "Cracked", test: g => ["crack", "denuvo"].includes(g.status) },
  { id: "denuvo", label: "Denuvo cracked", test: g => g.status === "denuvo" },
  { id: "workaround", label: "Workarounds", test: g => g.status === "workaround" },
  { id: "drm", label: "DRM-free / protection removed", test: g => ["gog", "drm_removed"].includes(g.status) },
  { id: "update", label: "Updates only", test: g => g.status === "update" },
  { id: "all", label: "Everything", test: () => true },
  { id: "archive", label: "Archive (older)", test: () => true },
];
const TERMS = {
  denuvo: "Denuvo is the strongest anti-piracy protection. Games with it often stay uncracked for months.",
  repack: "A repack is an existing crack, recompressed so it's smaller and easier to install. It doesn't crack anything new.",
  hypervisor: "A hypervisor workaround gets around Denuvo without removing it. You must disable Windows security features, which puts your PC at risk.",
};

let games = [];       // recent games
let archive = null;   // older games, once loaded
let archiveCount = 0;
let archiveLoading = null;
let filter = "news";
try { filter = localStorage.getItem("cw-filter") || filter; } catch (e) {}
const $ = id => document.getElementById(id);

function ago(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 3600) return "just now";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  const d = Math.floor(s / 86400);
  if (d === 1) return "yesterday";
  if (d < 31) return d + " days ago";
  const date = new Date(ts * 1000);
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short",
    year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
}
const postUrl = p => p.startsWith("http") ? p : "https://www.reddit.com" + p;
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
          <div>${t.text || (t.group ? `By <b>${esc(t.group)}</b>.` : "")}${t.size ? ` Size: ${esc(t.size)}.` : ""}
            <a href="${esc(postUrl(t.post))}" target="_blank" rel="noopener">Post</a></div></li>`).join("")}
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
        <a href="${esc(postUrl(latest.post))}" target="_blank" rel="noopener">Original post</a>
      </div>
      ${history}
    </div></article>`;
}

function loadArchive() {
  archiveLoading ??= fetch("data/archive.json", { cache: "no-store" })
    .then(r => r.json())
    .then(d => { archive = d.games; render(); })
    .catch(() => { archive = []; render(); });
}

function render() {
  const q = $("q").value.trim().toLowerCase();
  const f = FILTERS.find(x => x.id === filter) || FILTERS[0];
  // Searching looks through every year; the archive chip shows only older games.
  if ((q.length >= 2 || f.id === "archive") && !archive) loadArchive();
  const pool = f.id === "archive" ? (archive || []) : q.length >= 2 ? games.concat(archive || []) : games;
  $("chips").innerHTML = FILTERS.map(x => `<button class="chip" role="tab" data-f="${x.id}" aria-selected="${x.id === f.id}">
    ${x.label}<span class="n">${(x.id === "archive" ? archiveCount : games.filter(x.test).length).toLocaleString("en")}</span></button>`).join("");
  const shown = pool.filter(g => f.test(g) && (!q || g.name.toLowerCase().includes(q)));
  $("grid").innerHTML = shown.slice(0, 300).map(card).join("");
  const waiting = !archive && (f.id === "archive" || q.length >= 2);
  $("empty").textContent = waiting ? "Loading older games…" : "No games match that.";
  $("empty").hidden = shown.length > 0 && !waiting;
}

$("chips").addEventListener("click", e => {
  const b = e.target.closest("[data-f]");
  if (!b) return;
  filter = b.dataset.f;
  try { localStorage.setItem("cw-filter", filter); } catch (e) {}
  render();
});
$("q").addEventListener("input", render);

// The title goes back to the start: latest news, no search, top of the page.
$("home").addEventListener("click", e => {
  e.preventDefault();
  filter = "news";
  try { localStorage.setItem("cw-filter", filter); } catch (err) {}
  $("q").value = "";
  render();
  scrollTo({ top: 0, behavior: "smooth" });
});

// A cover that doesn't exist on Steam becomes the plain title tile.
document.addEventListener("error", e => {
  const img = e.target;
  if (img.tagName !== "IMG" || !img.closest(".cover")) return;
  const tile = document.createElement("div");
  tile.className = "noimg";
  tile.textContent = img.closest(".card").querySelector("h3").textContent;
  img.replaceWith(tile);
}, true);

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
    archiveCount = d.archive || 0;
    $("meta").textContent = `${games.length.toLocaleString("en")} games with recent news` +
      (archiveCount ? ` · ${archiveCount.toLocaleString("en")} older in the archive` : "") + ` · updated ${ago(d.built)}`;
    render();
  })
  .catch(() => {
    // Opened as a file or from an editor preview instead of through the web container.
    $("meta").innerHTML = location.protocol === "file:"
      ? 'This page only works through a web server: open <a href="http://localhost:8080">http://localhost:8080</a>.'
      : "No data yet. Check back in a few minutes.";
  });
