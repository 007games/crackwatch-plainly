"""Turn raw r/CrackWatch posts into plain-English game cards.

Reads  data/raw/posts.json      (written by fetch.py)
Writes docs/data/games.json   (the public site; PUBLIC_DIR overrides docs/)
Cache  data/steam_cache.json    (Steam store details, so each game is asked once)

Only facts and Steam/Reddit links go out: no NFOs, no download or repack links.

    python build.py          build once
    python build.py --loop   rebuild every few minutes (the container's mode)
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
RAW = DATA / "raw" / "posts.json"
OUT = Path(os.environ.get("PUBLIC_DIR", Path(__file__).resolve().parent.parent / "docs")) / "data" / "games.json"
CACHE = DATA / "steam_cache.json"

# ------------------------------------------------------------------ glossary
# Who released it, in words a non-scener understands.
GROUPS = {
    "rune": "scene", "tenoke": "scene", "razor1911": "scene", "razordox": "scene", "flt": "scene",
    "skidrow": "scene", "codex": "scene", "plaza": "scene", "doge": "scene", "tinyiso": "scene",
    "elamigos": "repack", "fitgirl": "repack", "dodi": "repack", "kaos": "repack", "darcktsiders": "repack",
    "vhstape": "scene", "badkarma": "scene", "dinobytes": "scene", "delight": "scene", "shame": "scene",
    "razor": "scene",
    "empress": "cracker", "0xzeon": "cracker", "artifact": "cracker", "voices38": "cracker", "denuvowo": "cracker",
    "p2p": "p2p", "x.x.riddick.x.x": "p2p", "x.x.riddick.x": "p2p", "i_know": "p2p", "insaneramzes": "p2p",
    "fckdrm": "p2p", "0verflow": "p2p", "bigmac": "p2p", "puls3": "p2p", "rg": "p2p",
    "gog": "gog",
}
GROUP_KIND_TEXT = {
    "scene": "a long-running release group",
    "repack": "a <span class=term data-t=repack>repacker</span>",
    "cracker": "an independent cracker",
    "p2p": "an independent uploader outside the organised scene",
    "gog": "the GOG store, which sells games with no copy protection at all",
    None: "a release group",
}
EDITION_WORDS = (r"(deluxe|complete|gold|ultimate|definitive|digital|premium|goty|directors? cut|edition|"
                 r"bundle|supporter|sammleredition|collectors?|standard|enhanced)")


def group_kind(group):
    return GROUPS.get((group or "").lower().replace("-", "").replace(" ", ""))


# ------------------------------------------------------------------ parsing
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
STEAM_APP = re.compile(r"store\.steampowered\.com/app/(\d+)")
SIZE = re.compile(r"Release Size\s*:?\s*([\d.,]+\s*[GMT]i?B)", re.I)


HYPERVISOR = re.compile(r"\b(hypervisor|hvisor|hv)\b", re.I)
WORKAROUND_WORDS = re.compile(r"\b(hypervisor|hvisor|hv|bypass(ed)?|experimental)\b", re.I)


def split_release(title, group=None):
    """'Game.Name.update.1.2-1.3-GROUP' -> ('Game Name', 'GROUP', is_update, '1.3').

    The group is the part after the last dash, unless the daily table already
    says who it is (groups like 'x.X.RIDDICK.X.x' contain dots and dashes).
    """
    t = title.strip()
    if group and t.lower().endswith("-" + group.lower()):
        t = t[:-len(group) - 1]
    elif not group:
        m = re.match(r"^(.*)-([A-Za-z0-9_]+)$", t)
        if m:
            t, group = m.group(1), m.group(2)
    t = re.sub(r"[._ -]+(REPACK|PROPER|MULTi\d*|READ\.?NFO|x\.X\.\w+\.X\.x|x X \w+ X x)\b.*$", "", t, flags=re.I)
    is_update = bool(re.search(r"(^|[._ ])update([._ ]|$)", t, re.I))
    version = None
    # The name ends where the version, build or update marker starts.
    v = re.search(r"[._ ](?:update|build|patch|v(?=\d)|(?=\d+(?:\.\d+)+))[._ ]?v?([\w.]+?)?(?:-([\w.]+))?$", t, re.I)
    if v:
        version = v.group(2) or v.group(1)
        t = t[:v.start()]
    name = re.sub(r"[._]+", " ", t).strip(" -")
    name = re.sub(r"\bDirectors Cut\b", "Director's Cut", name)
    if version and not re.search(r"\d", version):
        version = None
    return name, group, is_update, version


def norm(name):
    n = name.lower().replace("&", "and").replace("'", "").replace("’", "")
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    n = re.sub(r"\b" + EDITION_WORDS + r"\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def parse_review(s):
    m = re.match(r"\s*([\d.]+)%\s*\(([\d.,]+[km]?)\)", s or "", re.I)
    return {"percent": round(float(m.group(1))), "count": m.group(2)} if m else None


def events_from_post(p):
    """One raw post -> zero or more events (a game, what happened, who did it)."""
    flair = (p.get("link_flair_text") or "").strip()
    title, body = p.get("title") or "", html.unescape(p.get("selftext") or "")
    when = int(p.get("created_utc") or 0)
    post_url = "https://www.reddit.com" + (p.get("permalink") or "")
    base = {"date": when, "post": post_url}
    if p.get("removed_by_category"):
        return []

    if flair in ("Release", "Denuvo release"):
        name, group, is_update, version = split_release(title)
        steam = STEAM_APP.search(body)
        size = SIZE.search(body)
        notes = re.search(r"NOTES?:\s*(.+)", body, re.I)
        return [dict(base, game=name, group=group, steam=steam and steam.group(1),
                     kind="update" if is_update else ("denuvo" if flair == "Denuvo release" else "crack"),
                     version=version, size=size and size.group(1).replace("GiB", "GB"),
                     note=notes and notes.group(1).strip()[:200])]

    if flair == "Daily release":
        out = []
        section = None
        for line in body.splitlines():
            cells = [c.replace("**", "").strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2 or set(cells[0]) <= set(":- "):
                continue
            if cells[1].lower() == "group" or cells[0].lower() in ("game", "update"):
                section = cells[0].lower()
                continue
            if section is None:
                continue
            group = cells[1] or None
            steam = STEAM_APP.search(cells[2] if len(cells) > 2 else "") or re.search(r"/app/(\d+)", line)
            review = parse_review(cells[3] if len(cells) > 3 else "")
            if section == "update":
                name, group, _, version = split_release(cells[0], group)
                kind = "update"
            else:
                name = re.sub(r"\s*\((multi|eng)[^)]*\)?\s*$", "", cells[0], flags=re.I)  # "(MULTi10)"
                name, version = re.sub(r"\s+No( ?(DRM|Denuvo))?$", "", name, flags=re.I), None
                kind = "gog" if group_kind(group) == "gog" else ("repack" if group_kind(group) == "repack" else "crack")
            if HYPERVISOR.search(name):
                name, kind = re.sub(r"\s+", " ", WORKAROUND_WORDS.sub("", name)).strip(" -:"), "workaround"
            out.append(dict(base, game=name, group=group, steam=steam and steam.group(1),
                            kind=kind, version=version, review=review))
        return out

    if flair == "Denuvo Hypervisor Workaround":
        m = re.match(r"^(.*?)\s+(?:v?\d[\w.]*\s+)?-\s+(\w+)", title)
        name = m.group(1) if m else title.split(" - ")[0]
        name = re.sub(r"\s+", " ", WORKAROUND_WORDS.sub("", name)).strip(" -:")
        name = {"BL4": "Borderlands 4"}.get(name, name)
        group = m.group(2) if m else None
        return [dict(base, game=name, group=group, steam=None, kind="workaround")]

    if flair == "Article/News":
        m = re.match(r"^Denuvo removed from (?:the )?(?:Steam version of )?(.+)$", title, re.I)
        if m:
            return [dict(base, game=m.group(1).strip(), group=None, steam=None, kind="drm_removed")]
    return []


# ------------------------------------------------------------------ Steam
def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "crackwatch-plain/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


class Steam:
    def __init__(self):
        self.cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
        self.calls = 0

    def save(self):
        CACHE.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")

    def _get(self, url):
        self.calls += 1
        time.sleep(1.6)  # Steam allows about 200 requests per 5 minutes
        return http_json(url)

    def _search_once(self, term, want):
        q = urllib.parse.quote(term)
        items = self._get(f"https://store.steampowered.com/api/storesearch/?term={q}&l=english&cc=US").get("items") or []
        hit = next((i for i in items if norm(i["name"]) == want), None) or \
              next((i for i in items if norm(i["name"]).startswith(want) or want.startswith(norm(i["name"]))), None)
        return str(hit["id"]) if hit else None

    def search(self, name):
        """Steam's search often finds nothing for 'X Deluxe Edition' but finds 'X'."""
        key = "search2:" + name.lower()
        if key not in self.cache:
            want = norm(name)
            try:
                hit = self._search_once(name, want)
                plain = re.sub(r"\s+", " ", re.sub(r"\b" + EDITION_WORDS + r"\b|\b(pack|rerelease|year \d+)\b", " ",
                                                  name, flags=re.I)).strip()
                if not hit and plain.lower() != name.lower() and plain:
                    hit = self._search_once(plain, want)
            except Exception as e:
                print("steam search failed:", name, e)
                return None
            self.cache[key] = hit
        return self.cache[key]

    def details(self, appid):
        key = "app:" + appid
        if key not in self.cache:
            try:
                res = self._get(f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=us&l=english")
                d = (res.get(appid) or {}).get("data") or {}
                price = d.get("price_overview") or {}
                self.cache[key] = {
                    "name": d.get("name"),
                    "image": d.get("header_image"),
                    "blurb": html.unescape(d.get("short_description") or ""),
                    "genres": [g["description"] for g in d.get("genres") or []][:3],
                    "released": (d.get("release_date") or {}).get("date"),
                    "price": price.get("final_formatted") or ("Free" if d.get("is_free") else None),
                    "drm": d.get("drm_notice"),
                }
            except Exception as e:
                print("steam details failed:", appid, e)
                return None
        return self.cache[key]


def parse_date(s):
    for fmt in ("%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%B %d, %Y", "%b %Y", "%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    return None


# ------------------------------------------------------------------ plain English
STATUS_RANK = {"denuvo": 6, "crack": 5, "gog": 4, "repack": 3, "workaround": 2, "drm_removed": 1, "update": 0}
STATUS_LABEL = {
    "denuvo": "Denuvo cracked", "crack": "Cracked", "gog": "DRM-free", "repack": "Repack",
    "workaround": "Workaround only", "drm_removed": "Protection removed", "update": "Update",
}


def who(group):
    if not group:
        return ""
    return f"<b>{html.escape(group)}</b>, {GROUP_KIND_TEXT[group_kind(group)]}"


def sentence(ev, launched):
    g = ev.get("group")
    after = ""
    if launched:
        days = (datetime.fromtimestamp(ev["date"], timezone.utc) - launched).days
        if 0 <= days <= 60:
            span = f"<b>{days} day{'s' if days != 1 else ''}</b>"
            after = (" — on <b>launch day</b>" if not days else
                     f" — just {span} after launch" if days <= 14 else f", {span} after launch")
        elif days > 365:
            after = f" (the game came out in {launched.year})"
    k = ev["kind"]
    if k == "denuvo":
        return (f"This game was protected by <span class=term data-t=denuvo>Denuvo</span>, the toughest anti-piracy "
                f"protection around, and it has now been fully removed by {who(g)}{after}.")
    if k == "crack":
        return f"A cracked version, playable without buying it, was released by {who(g)}{after}."
    if k == "gog":
        return f"A DRM-free copy is now out, taken from {GOG_TEXT}{after}."
    if k == "repack":
        name = f"<b>{html.escape(g)}</b>" if g else "Someone"
        return (f"{name} put out a <span class=term data-t=repack>repack</span>: a smaller, easier-to-install "
                f"version of an existing crack{after}.")
    if k == "update":
        v = f" to version <b>{html.escape(ev['version'])}</b>" if ev.get("version") else ""
        return f"An update{v} is out for the cracked version, released by {who(g)}."
    if k == "workaround":
        return (f"<b>Not a real crack.</b> {who(g) or 'Someone'} released a <span class=term data-t=hypervisor>hypervisor "
                f"workaround</span>: it only runs if you switch off Windows security features, which puts your PC at risk.")
    if k == "drm_removed":
        return "The publisher removed Denuvo from the official game. That usually means it is cracked soon after, or already is."
    return ""


GOG_TEXT = "<b>GOG</b>, a store that sells games with no copy protection"


def build():
    if not RAW.exists():
        print("no raw posts yet:", RAW)
        return
    posts = json.loads(RAW.read_text(encoding="utf-8"))
    events = [e for p in posts.values() for e in events_from_post(p) if e.get("game")]
    steam = Steam()

    # A name posted without a Steam link often appears elsewhere with one.
    known = {norm(e["game"]): e["steam"] for e in events if e.get("steam")}
    games = {}
    for ev in sorted(events, key=lambda e: e["date"]):
        appid = ev.get("steam") or known.get(norm(ev["game"])) or steam.search(ev["game"])
        key = "steam:" + appid if appid else "name:" + norm(ev["game"])
        g = games.setdefault(key, {"id": key, "name": ev["game"], "steam": appid, "events": []})
        g["events"].append(ev)

    out = []
    for g in games.values():
        info = steam.details(g["steam"]) if g["steam"] else None
        launched = parse_date(info.get("released")) if info else None
        evs = sorted(g["events"], key=lambda e: -e["date"])
        # Same release in a single post and again in a daily table: keep one.
        seen, uniq = set(), []
        for e in evs:
            k = (e["kind"], (e.get("group") or "").lower(), e.get("version"))
            if k not in seen:
                seen.add(k)
                uniq.append(e)
        # The strongest news wins; within it, credit whoever did it first.
        best = max(uniq, key=lambda e: (STATUS_RANK[e["kind"]], -e["date"]))
        review = next((e["review"] for e in uniq if e.get("review")), None)
        out.append({
            "id": g["id"],
            "name": (info or {}).get("name") or g["name"],
            "status": best["kind"],
            "label": STATUS_LABEL[best["kind"]],
            "updated": max(e["date"] for e in uniq),
            "summary": sentence(best, launched),
            "image": (info or {}).get("image"),
            "blurb": (info or {}).get("blurb"),
            "genres": (info or {}).get("genres") or [],
            "released": (info or {}).get("released"),
            "price": (info or {}).get("price"),
            "review": review,
            "steam_url": f"https://store.steampowered.com/app/{g['steam']}/" if g["steam"] else None,
            "timeline": [{
                "date": e["date"], "kind": e["kind"], "label": STATUS_LABEL[e["kind"]],
                "text": sentence(e, launched), "post": e["post"], "size": e.get("size"),
                "group": e.get("group"),
            } for e in uniq],
        })
        if steam.calls and steam.calls % 25 == 0:
            steam.save()
    steam.save()
    out.sort(key=lambda g: -g["updated"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps({"built": int(time.time()), "games": out}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    print(f"{datetime.now():%Y-%m-%d %H:%M} built {len(out)} games from {len(posts)} posts "
          f"({steam.calls} Steam calls)", flush=True)


if __name__ == "__main__":
    if "--loop" in sys.argv:
        last = None
        while True:
            try:
                mtime = RAW.stat().st_mtime if RAW.exists() else None
                if mtime != last:
                    build()
                    last = mtime
            except Exception as e:
                print("build failed:", type(e).__name__, e, flush=True)
            time.sleep(int(os.environ.get("CHECK_EVERY", "60")))
    else:
        build()
