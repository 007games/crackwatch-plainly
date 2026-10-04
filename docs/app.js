// Renders data/games.json (recent) as a sortable table; data/archive.json (older) loads on demand.
const FILTERS = [
  { id: "news", label: "Latest news", test: g => g.status !== "update" },
  { id: "cracked", label: "Cracked", test: g => ["crack", "denuvo"].includes(g.status) },
  { id: "denuvo", label: "Denuvo cracked", test: g => g.status === "denuvo" },
  { id: "workaround", label: "Workarounds", test: g => g.status === "workaround" },
  { id: "drm", label: "DRM-free / protection removed", test: g => ["gog", "drm_removed"].includes(g.status) },
  { id: "update", label: "Updates", test: g => g.status === "update" },
  { id: "all", label: "Everything", test: () => true },
  { id: "archive", label: "Archive (older)", test: () => true },
];
const TERMS = {
  denuvo: "Denuvo is the strongest anti-piracy protection. Games with it often stay uncracked for months.",
  repack: "A repack is an existing crack, recompressed so it's smaller and easier to install. It doesn't crack anything new.",
  hypervisor: "A hypervisor workaround gets around Denuvo without removing it. You must disable Windows security features, which puts your PC at risk.",
};
const KIND_TEXT = { scene: "release group", repack: "repacker", cracker: "independent cracker",
                    p2p: "independent uploader", gog: "DRM-free store", chinese: "Chinese group" };
const PAGE = 100;

let games = [];       // recent games
let archive = null;   // older games, once loaded
let archiveCount = 0;
let archiveLoading = null;
let filter = "news";
let sort = { key: "updated", dir: -1 };
let limit = PAGE;
let shown = [];
const open = new Set();
try { filter = localStorage.getItem("cw-filter") || filter; } catch (e) {}
const $ = id => document.getElementById(id);

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const postUrl = p => p.startsWith("http") ? p : "https://www.reddit.com" + p;
const fmtDate = ts => new Date(ts * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const price = g => !g.price ? null : /free/i.test(g.price) ? 0 : parseFloat(g.price.replace(/[^\d.]/g, "")) || null;
const dash = `<span class="dim">–</span>`;

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

// ------------------------------------------------------------------ stats strip
function stats() {
  const week = Date.now() / 1000 - 7 * 86400;
  const isCrack = k => k === "crack" || k === "denuvo";
  const crackedWeek = games.filter(g => g.timeline.some(t => isCrack(t.kind) && t.date >= week)).length;
  const denuvo = games.filter(g => g.status === "denuvo").sort((a, b) => b.updated - a.updated);
  const timed = games.filter(g => g.days != null && isCrack(g.status)).map(g => g.days).sort((a, b) => a - b);
  const fastest = games.filter(g => g.status === "denuvo" && g.days != null).sort((a, b) => a.days - b.days)[0];
  const tile = (v, k, s) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div><div class="s">${s || "&nbsp;"}</div></div>`;
  $("stats").innerHTML =
    tile(crackedWeek, "games cracked in the last 7 days") +
    tile(denuvo.length, "Denuvo cracks in the last 120 days", denuvo[0] ? "Latest: " + esc(denuvo[0].name) : "") +
    (fastest
      ? tile(fastest.days + (fastest.days === 1 ? " day" : " days"), "fastest Denuvo crack", esc(fastest.name))
      : tile(timed.length ? timed[timed.length >> 1] + " days" : "–", "typical time to crack", "median of recent cracks")) +
    tile((games.length + archiveCount).toLocaleString("en"), "games tracked", "since October 2016");
}

// ------------------------------------------------------------------ table
function sortValue(g, key) {
  if (key === "name") return g.name.toLowerCase();
  if (key === "group") return (g.group || "").toLowerCase() || null;
  if (key === "price") return price(g);
  if (key === "rating") return g.review ? g.review.percent : null;
  return g[key] ?? null;
}

function row(g, i) {
  const img = g.image ? `<img class="cap" src="${esc(g.image)}" alt="" loading="lazy">` : `<span class="cap blank">no image</span>`;
  const genres = g.genres && g.genres.length ? `<div class="sub">${esc(g.genres.join(", "))}</div>` : "";
  let days = dash;
  if (g.days != null) {
    // Square-root scale: the difference between 2 and 20 days matters more than 300 vs 400.
    const w = Math.max(4, Math.min(100, Math.sqrt(g.days / 365) * 100));
    const cls = g.days <= 7 ? "fast" : g.days > 90 ? "slow" : "";
    days = `<div class="days"><span>${g.days}</span><span class="track"><span class="fill ${cls}" style="width:${w}%"></span></span></div>`;
  }
  const p = g.review && g.review.percent;
  const rating = g.review
    ? `<span class="rating ${p >= 70 ? "good" : p >= 40 ? "mixed" : "bad"}" title="${esc(g.review.count)} Steam reviews">${p}%</span>` : dash;
  const isOpen = open.has(g.id);
  return `<tr class="g${isOpen ? " open" : ""}" data-i="${i}" aria-expanded="${isOpen}">
    <td class="c-img">${img}</td>
    <td class="c-name"><div class="name">${esc(g.name)}</div>${genres}</td>
    <td class="c-status"><span class="badge s-${g.status}">${esc(g.label)}</span></td>
    <td class="c-group">${g.group ? `<div class="group">${esc(g.group)}</div><div class="kind">${KIND_TEXT[g.group_kind] || ""}</div>` : dash}</td>
    <td class="c-days num">${days}</td>
    <td class="c-date num" title="${fmtDate(g.updated)}">${ago(g.updated)}</td>
    <td class="c-price num">${g.price ? esc(g.price) : dash}</td>
    <td class="c-rating num">${rating}</td>
  </tr>${isOpen ? detail(g) : ""}`;
}

function detail(g) {
  const tl = g.timeline.map(t => `<li>
      <span class="t-date">${fmtDate(t.date)}</span>
      <span><span class="badge s-${t.kind}">${esc(t.label)}</span></span>
      <span>${t.text || (t.group ? `By <b>${esc(t.group)}</b>.` : "")}${t.size ? ` Size: ${esc(t.size)}.` : ""}
        <a href="${esc(postUrl(t.post))}" target="_blank" rel="noopener">Post</a></span></li>`).join("");
  return `<tr class="detail"><td colspan="8">
    <p class="summary">${g.summary}</p>
    <div class="links">
      ${g.steam_url ? `<a href="${esc(g.steam_url)}" target="_blank" rel="noopener">Steam page</a>` : ""}
      <a href="${esc(postUrl(g.timeline[0].post))}" target="_blank" rel="noopener">Original post</a>
      ${g.released ? `<span class="dim">Released ${esc(g.released)}</span>` : ""}
    </div>
    ${g.timeline.length > 1 ? `<ul class="timeline">${tl}</ul>` : ""}
  </td></tr>`;
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
  // Searching looks through every year; the archive tab shows only older games.
  if ((q.length >= 2 || f.id === "archive") && !archive) loadArchive();
  const pool = f.id === "archive" ? (archive || []) : q.length >= 2 ? games.concat(archive || []) : games;

  $("chips").innerHTML = FILTERS.map(x => `<button class="tab" role="tab" data-f="${x.id}" aria-selected="${x.id === f.id}">
    ${x.label}<span class="n">${(x.id === "archive" ? archiveCount : games.filter(x.test).length).toLocaleString("en")}</span></button>`).join("");

  shown = pool.filter(g => f.test(g) && (!q || g.name.toLowerCase().includes(q)));
  const { key, dir } = sort;
  shown.sort((a, b) => {
    const va = sortValue(a, key), vb = sortValue(b, key);
    if (va === vb) return b.updated - a.updated;
    if (va == null) return 1;   // unknowns last, whichever way
    if (vb == null) return -1;
    return (va < vb ? -1 : 1) * dir;
  });
  document.querySelectorAll("th.sortable").forEach(th => {
    if (th.dataset.sort === key) th.setAttribute("aria-sort", dir > 0 ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  });

  const waiting = !archive && (f.id === "archive" || q.length >= 2);
  $("rows").innerHTML = shown.length
    ? shown.slice(0, limit).map(row).join("")
    : `<tr class="empty"><td colspan="8">${waiting ? "Loading older games…" : "No games match that."}</td></tr>`;
  $("more").hidden = shown.length <= limit;
  $("meta").textContent = shown.length
    ? `Showing ${Math.min(limit, shown.length).toLocaleString("en")} of ${shown.length.toLocaleString("en")}` +
      (waiting ? " · loading older games…" : "")
    : "";
}

// ------------------------------------------------------------------ events
$("chips").addEventListener("click", e => {
  const b = e.target.closest("[data-f]");
  if (!b) return;
  filter = b.dataset.f;
  limit = PAGE;
  try { localStorage.setItem("cw-filter", filter); } catch (err) {}
  render();
});
$("q").addEventListener("input", () => { limit = PAGE; render(); });
$("more").addEventListener("click", () => { limit += PAGE; render(); });

document.querySelector("thead").addEventListener("click", e => {
  const th = e.target.closest("th.sortable");
  if (!th) return;
  const key = th.dataset.sort;
  // A→Z for text, fastest and cheapest first for days and price, highest/newest first otherwise.
  const first = ["name", "group", "days", "price"].includes(key) ? 1 : -1;
  sort = sort.key === key ? { key, dir: -sort.dir } : { key, dir: first };
  limit = PAGE;
  render();
});

$("rows").addEventListener("click", e => {
  if (e.target.closest("a")) return;
  const tr = e.target.closest("tr.g");
  if (!tr) return;
  const g = shown[+tr.dataset.i];
  open.has(g.id) ? open.delete(g.id) : open.add(g.id);
  render();
});

// The title goes back to the start: latest news, no search, newest first, top of the page.
$("home").addEventListener("click", e => {
  e.preventDefault();
  filter = "news";
  sort = { key: "updated", dir: -1 };
  limit = PAGE;
  open.clear();
  try { localStorage.setItem("cw-filter", filter); } catch (err) {}
  $("q").value = "";
  render();
  scrollTo({ top: 0, behavior: "smooth" });
});

// A cover that doesn't exist on Steam becomes a blank tile.
document.addEventListener("error", e => {
  const img = e.target;
  if (img.tagName !== "IMG" || !img.classList.contains("cap")) return;
  const tile = document.createElement("span");
  tile.className = "cap blank";
  tile.textContent = "no image";
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
    stats();
    render();
  })
  .catch(() => {
    $("rows").innerHTML = `<tr class="empty"><td colspan="8">${location.protocol === "file:"
      ? 'This page only works through a web server: open <a href="http://localhost:8080">http://localhost:8080</a>.'
      : "No data yet. Check back in a few minutes."}</td></tr>`;
  });
