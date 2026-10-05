"""Turn raw r/CrackWatch posts into plain-English game cards.

Reads  data/raw/posts.json       (recent posts, written by fetch.py)
       data/raw/archive.json     (older posts, written by backfill.py)
Writes docs/data/games.json      (games with news in the last RECENT_DAYS, loaded first)
       docs/data/archive.json    (all other games, compact, loaded on demand)
Cache  data/steam_cache.json     (Steam store details, so each game is asked once)

Only facts and Steam/Reddit links go out: no NFOs, no download or repack links.

    python build.py                     build, with at most 5 minutes of Steam lookups
    python build.py --steam-minutes 18  more lookups (the backfill's mode)

Lookups that do not fit in the time are left for a later build; the last line
says how many are pending.
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
RAW = DATA / "raw" / "posts.json"
ARCHIVE_RAW = DATA / "raw" / "archive.json"
PUBLIC = Path(os.environ.get("PUBLIC_DIR", Path(__file__).resolve().parent.parent / "docs")) / "data"
CACHE = DATA / "steam_cache.json"
LOCK = DATA / "build.lock"
RECENT_DAYS = 120

# ------------------------------------------------------------------ glossary
# Who released it, in words a non-scener understands.
GROUPS = {
    "rune": "scene", "tenoke": "scene", "razor1911": "scene", "razordox": "scene", "flt": "scene",
    "skidrow": "scene", "codex": "scene", "plaza": "scene", "doge": "scene", "tinyiso": "scene",
    "elamigos": "repack", "fitgirl": "repack", "dodi": "repack", "kaos": "repack", "darcktsiders": "repack",
    "vhstape": "scene", "badkarma": "scene", "dinobytes": "scene", "delight": "scene", "shame": "scene",
    "razor": "scene", "cpy": "scene", "conspir4cy": "scene", "reloaded": "scene", "hoodlum": "scene",
    "prophet": "scene", "darksiders": "scene", "hi2u": "scene", "simplex": "scene", "relaxed": "scene",
    "steampunks": "cracker", "baldman": "cracker", "voksi": "cracker", "revolt": "cracker",
    "3dm": "chinese", "3dmgame": "chinese", "aloha": "chinese", "lmao": "chinese",
    "empress": "cracker", "0xzeon": "cracker", "artifact": "cracker", "voices38": "cracker", "denuvowo": "cracker",
    "p2p": "p2p", "x.x.riddick.x.x": "p2p", "x.x.riddick.x": "p2p", "i_know": "p2p", "insaneramzes": "p2p",
    "fckdrm": "p2p", "0verflow": "p2p", "bigmac": "p2p", "puls3": "p2p", "rg": "p2p",
    "gog": "gog",
}
GROUP_KIND_TEXT = {
    "scene": "a long-running release group",
    "repack": "a <span class=term data-t=repack>repacker</span>",
    "cracker": "an independent cracker",
    "chinese": "a Chinese cracking group",
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
    t = re.sub(r"[\s._]+(torrent|cracked)$", "", title.strip(), flags=re.I)
    if group and t.lower().endswith("-" + group.lower()):
        t = t[:-len(group) - 1]
    elif not group:
        m = re.match(r"^(.*)-([A-Za-z0-9_]+)$", t)
        if m:
            t, group = m.group(1), m.group(2)
    t = re.sub(r"[._ -]+(REPACK|PROPER|MULTi\d*|READ\.?NFO|x\.X\.\w+\.X\.x|x X \w+ X x)\b.*$", "", t, flags=re.I)
    # 2016-era titles: "3DMGAME-Name-3DM torrent", "Name Cracked", "Name Universal Crack Only SSE".
    t = re.sub(r"^3DMGAME[-._ ]", "", t, flags=re.I)
    t = re.sub(r"([._ -]+(cracked|crack|only|torrent|universal|sse|fix|working|confirmed))+$", "", t, flags=re.I)
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


MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Words around a name that say how it was released, not what the game is called.
NAME_JUNK = re.compile(
    r"(?:\s+|[._-])(?:crack\s?fix|hot\s?fix|crack\s+only|online\s+fix|steamworks\s+fix|cracked|crack|fix|x86|x64|rip|"
    r"to\s+cracked|bypass(?:ed)?|prepack|inc(?:l|luding)?\.?(?:\s+[\w']+)*|all\s+dlc'?s?|dlcs?|v\d+(?:\.\d+)*|"
    r"public\s+beta\s*\d*|working|dirfix|and)$", re.I)
# A question or a sentence, not a release: early posts often carried a "Release" label anyway.
SENTENCE = re.compile(
    r"\?|^(will|is|does|do|did|can|could|how\s+(?:do|to\s+(?:fix|play|install|use))|why|what|when\s+will|anyone|has|have|"
    r"need|test\b|official|remove|thanks?|thank\s+you|i\s|i'm|my\s|we\s|the\s+[‘'\"])|\b(please|pls|plz|help\s+me\s+with|"
    r"cracked\s+it|odds|says\s+this|error|website|crashing|not\s+working|feed|thanks|appreciation|amateurs|question|for\s+free|free\s+(?:on|for|games?|crack|version)|leaked|tutorial|"
    r"bitcoin|script|now\s+you\s+can|available|claim|if\s+you\s+want|can\s+be\s+applied|drm[\s-]free\s+on|"
    r"will\s+be\s+on|psd\s+files)\b", re.I)
SCENE_TITLE = re.compile(r"^[\w.()'&+!,]+-[\w.]+$")  # no / ? : so a pasted link never passes
# Never on the site: anything naming where to get files, or a web address.
OFF_LIMITS = re.compile(r"torrent|download|magnet|\bigg|\bwww\b|\.com\b|\bcom/|\bcom\s|\bco kr\b|youtube|imgur|\blink\b", re.I)


def looks_like_release(title):
    if SCENE_TITLE.match(title.strip()):
        return True
    words = len(re.sub(r"[\[({].*?[\])}]", "", title).split())  # "(v1.06, MULTi11)" doesn't count
    return words <= 9 and not SENTENCE.search(title)
KNOWN_BY = re.compile(r"\s+(?:(?:crack|crackfix|fix|bypass|prepack|repack|hypervisor)(?:\s+v\d+)?\s+)?by\s+[\w.-]+(?:\s+and\s+[\w.-]+)?$", re.I)
NOT_A_GAME = re.compile(r"^(poll|generic|weekly|daily|\[crack watch\])\b", re.I)


def clean_name(name):
    """'Pro Evolution Soccer 2017 CRACKFIX', 'X (Hypervisor) (INTEL & AMD)', '[X](nfo-link)' -> the game's name."""
    n = MD_LINK.sub(r"\1", name or "").replace("\\", "").replace("*", "")  # markdown: "RPG\!\!", "**CRACKFIX**"
    n = re.sub(r"https?://\S+", "", n).strip()
    if " " not in n and re.search(r"[._]", n):  # a release name in a table: "When.Ski.Lifts.Go.Wrong"
        n = split_release(n)[0]
    n = re.split(r"\s+[-–|]\s+(?:darck|kaos|fitgirl|dodi|corepack|elamigos|multi\d*|lossless)\b|\s+\|\s+|\s+\+\s+|\s+—\s+",
                 n, maxsplit=1, flags=re.I)[0]
    n = re.sub(r"\s*[(\[{][^)\]}]*[)\]}]?", " ", n)  # (Hypervisor), [FitGirl Repack], {MULTI15}
    n = re.sub(r"\s+", " ", n).strip(" -–:,.|")
    for _ in range(4):  # "Name Online Fix x86", "Name Crack Only V2"
        before = n
        n = KNOWN_BY.sub("", n) if re.search(r"\b(crack|fix|bypass|prepack|repack|hypervisor)(\s+v\d+)?\s+by\b", n, re.I) or \
            re.search(r"\sby\s+(" + "|".join(map(re.escape, KNOWN_BYLINE)) + r")\b", n, re.I) else n
        n = NAME_JUNK.sub("", n).strip(" -–:,.")
        last = n.rsplit(" ", 1)
        if len(last) == 2 and last[1].lower() in GROUPS:  # "Octopath Traveler CPY"
            n = last[0]
        if n == before:
            break
    return n


KNOWN_BYLINE = ["denuvowo", "kirigiri", "zaxrow", "uberpsyx", "corepack", "masquerade", "empress", "voices38", "0xzeon"]


def events_from_post(p):
    """The events of a post, with clean game names; junk titles give nothing."""
    out = []
    for e in _events_from_post(p):
        e["game"] = clean_name(e["game"])
        if e["game"] and len(e["game"]) > 1 and not NOT_A_GAME.match(e["game"]) \
                and not OFF_LIMITS.search(e["game"]) \
                and not OFF_LIMITS.search(re.sub(r"selective\s+download", "", p.get("title") or "", flags=re.I)):
            out.append(e)
    return out


def _events_from_post(p):
    """One raw post -> zero or more events (a game, what happened, who did it)."""
    flair = (p.get("link_flair_text") or "").strip()
    title, body = p.get("title") or "", html.unescape(p.get("selftext") or "")
    when = int(p.get("created_utc") or 0)
    post_url = "https://www.reddit.com" + (p.get("permalink") or "")
    base = {"date": when, "post": post_url}
    if p.get("removed_by_category"):
        return []
    if re.match(r"^Daily Releases?\b", title, re.I):  # 2017 tables carried the "Release" label
        flair = "Daily release"
    if flair not in ("Daily release", "Repack") and not looks_like_release(title):
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
            # Older tables link each name to its NFO page: keep only the text, never the address.
            # Done on the whole line first: a name can hold a "|" inside its link text.
            plain = MD_LINK.sub(lambda m: m.group(1).replace("|", "/"), line)
            cells = [c.replace("**", "").strip() for c in plain.strip().strip("|").split("|")]
            if len(cells) < 2 or set(cells[0]) <= set(":- "):
                continue
            if cells[1].lower() == "group" or cells[0].lower() in ("game", "update"):
                section = cells[0].lower()
                continue
            if section is None:
                continue
            group = cells[1] if cells[1] and len(cells[1]) <= 30 and not re.search(r"https?:|\]\(", cells[1]) else None
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

    if flair == "Repack":
        # One post can list several: "Game A (v1.0, MULTi9) 3.8 GB / Game B (MULTi2) 10 GB [FitGirl Repack]".
        author = (p.get("author") or "").lower()
        group = next((g for k, g in REPACKERS.items() if k in author or k in title.lower()), None)
        out = []
        for part in re.split(r"\s+/\s+", re.sub(r"\s*[(\[{][^)\]}]*[)\]}]", " ", title)):
            part = re.sub(r"\s+(?:from\s+)?[\d.,]+\s*[GM]B\b.*$", "", part, flags=re.I)  # "3.8 GB", "from 6.7 GB"
            name = re.split(r"\s+[–-]\s+v?\d|,\s*v\d|\s+v\d+[.\d]*|\s+build\s+\d", part, maxsplit=1, flags=re.I)[0]
            name = name.strip(" -–:")
            if name and looks_like_release(name):
                out.append(dict(base, game=name, group=group, steam=None, kind="repack"))
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
STEAM_HEADER = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{}/header.jpg"
REPACKERS = {"fitgirl": "FitGirl", "dodi": "DODI", "kaos": "KaOs", "elamigos": "ElAmigos", "xatab": "xatab"}


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "crackwatch-plain/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


class Steam:
    def __init__(self, minutes):
        self.cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
        self.calls = 0
        self.pending = 0  # lookups left for a later build
        self.deadline = time.time() + minutes * 60

    def out_of_time(self):
        if time.time() > self.deadline:
            self.pending += 1
            return True
        return False

    def save(self):
        CACHE.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")

    def _get(self, url):
        self.calls += 1
        if self.calls % 50 == 0:
            self.save()
        time.sleep(1.6)  # Steam allows about 200 requests per 5 minutes
        try:
            return http_json(url)
        except urllib.error.HTTPError as e:
            if e.code == 429:  # told to slow down: stop asking for this build
                self.deadline = 0
            raise

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
            if self.out_of_time():
                return None
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
            if self.out_of_time():
                return None
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
    by = f" by {who(g)}" if g else ""
    if k == "denuvo":
        return (f"This game was protected by <span class=term data-t=denuvo>Denuvo</span>, the toughest anti-piracy "
                f"protection around, and it was fully removed{by}{after}.")
    if k == "crack":
        return f"A cracked version, playable without buying it, was released{by}{after}."
    if k == "gog":
        return f"A DRM-free copy came out, taken from {GOG_TEXT}{after}."
    if k == "repack":
        name = f"<b>{html.escape(g)}</b>" if g else "Someone"
        return (f"{name} put out a <span class=term data-t=repack>repack</span>: a smaller, easier-to-install "
                f"version of an existing crack{after}.")
    if k == "update":
        v = f" to version <b>{html.escape(ev['version'])}</b>" if ev.get("version") else ""
        return f"An update{v} for the cracked version was released{by}."
    if k == "workaround":
        return (f"<b>Not a real crack.</b> {who(g) or 'Someone'} released a <span class=term data-t=hypervisor>hypervisor "
                f"workaround</span>: it only runs if you switch off Windows security features, which puts your PC at risk.")
    if k == "drm_removed":
        return "The publisher removed Denuvo from the official game. That usually means it is cracked soon after, or already is."
    return ""


GOG_TEXT = "<b>GOG</b>, a store that sells games with no copy protection"


def days_to_crack(events, launched, status):
    """Days from launch to the first real crack, for the table's "Days to crack" column.

    For a Denuvo game that is the first Denuvo crack by a named group: an unsigned
    post on launch day is news that it *has* Denuvo, or a fake. A Denuvo "crack"
    within two days of launch can't be told apart from those from the posts alone.
    """
    if status == "denuvo":
        cracks = [e["date"] for e in events if e["kind"] == "denuvo" and e.get("group")]
    else:
        cracks = [e["date"] for e in events if e["kind"] in ("crack", "denuvo")]
    if not cracks or not launched:
        return None
    days = (datetime.fromtimestamp(min(cracks), timezone.utc) - launched).days
    if status == "denuvo" and days < 2:
        return None
    return days if 0 <= days <= 3650 else None


# A re-release of a game cracked before: "Reloaded Edition", "Enhanced", "Remastered".
REISSUE_WORDS = re.compile(r"\b(reloaded|remaster(ed)?|redux|anniversary|hd|rerelease|re release|enhanced)\b")


def base_name(name):
    return re.sub(r"\s+", " ", REISSUE_WORDS.sub(" ", norm(name))).strip()


def drop_reissue_days(games):
    """No "days to crack" for a re-release whose original was already cracked before it came out."""
    first_crack = {}
    for g in games:
        cracks = [t["date"] for t in g["timeline"] if t["kind"] in ("crack", "denuvo")]
        if cracks:
            b = base_name(g["name"])
            first_crack.setdefault(b, []).append((min(cracks), g["id"]))
    for g in games:
        if g["days"] is None or not g["launched"]:
            continue
        earlier = [d for d, gid in first_crack.get(base_name(g["name"]), []) if gid != g["id"] and d < g["launched"]]
        if earlier:
            g["days"] = None


FORBIDDEN = re.compile(r"predb\.|xrel\.to|pastebin|magnet:|torrent|nfo\.html|ibb\.co|imgur|mega\.nz|1fichier|"
                       r"igg-?games|fitgirl-repacks|dodi-repacks|steamrip|gofile|rapidgator", re.I)


def load_posts():
    posts = {}
    for path in (ARCHIVE_RAW, RAW):  # recent posts win over their archived copy
        if path.exists():
            posts.update(json.loads(path.read_text(encoding="utf-8")))
    return posts


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def build(steam_minutes=5):
    posts = load_posts()
    if not posts:
        print("no raw posts yet:", RAW)
        return
    events = [e for p in posts.values() for e in events_from_post(p) if e.get("game")]
    steam = Steam(steam_minutes)

    # A name posted without a Steam link often appears elsewhere with one.
    known = {norm(e["game"]): e["steam"] for e in events if e.get("steam")}
    # Newest first, so the Steam time goes to the games people look at first.
    events.sort(key=lambda e: -e["date"])
    games = {}
    for ev in events:
        appid = ev.get("steam") or known.get(norm(ev["game"])) or steam.search(ev["game"])
        key = "steam:" + appid if appid else "name:" + norm(ev["game"])
        g = games.setdefault(key, {"id": key, "name": ev["game"], "steam": appid, "events": []})
        g["events"].append(ev)

    out = []
    for g in games.values():
        info = steam.details(g["steam"]) if g["steam"] else None
        launched = parse_date(info.get("released")) if info else None
        # Same release in its own post and again in a later daily table: keep the first,
        # so the history shows when it really happened.
        seen, uniq = set(), []
        for e in sorted(g["events"], key=lambda e: e["date"]):
            k = (e["kind"], (e.get("group") or "").lower(), e.get("version"))
            if k not in seen:
                seen.add(k)
                uniq.append(e)
        uniq.reverse()  # newest first for the page
        # The strongest news wins; within it, credit whoever did it first.
        best = max(uniq, key=lambda e: (STATUS_RANK[e["kind"]], -e["date"]))
        review = next((e["review"] for e in uniq if e.get("review")), None)
        out.append({
            "id": g["id"],
            "name": (info or {}).get("name") or g["name"],
            "status": best["kind"],
            "label": STATUS_LABEL[best["kind"]],
            "group": best.get("group"),
            "group_kind": group_kind(best.get("group")),
            "days": days_to_crack(uniq, launched, best["kind"]),
            "launched": int(launched.timestamp()) if launched else None,
            "updated": max(e["date"] for e in uniq),
            "summary": sentence(best, launched),
            # Without looked-up details, Steam's usual header address; the page hides it if missing.
            "image": (info or {}).get("image") or (g["steam"] and STEAM_HEADER.format(g["steam"])),
            "genres": (info or {}).get("genres") or [],
            "released": (info or {}).get("released"),
            "price": (info or {}).get("price"),
            "review": review,
            "steam_url": f"https://store.steampowered.com/app/{g['steam']}/" if g["steam"] else None,
            "timeline": [{
                "date": e["date"], "kind": e["kind"], "label": STATUS_LABEL[e["kind"]],
                "text": sentence(e, launched), "post": e["post"].removeprefix("https://www.reddit.com"),
                "size": e.get("size"), "group": e.get("group"),
            } for e in uniq],
        })
    steam.save()
    drop_reissue_days(out)
    # Safety net: nothing that points at files or NFO pages is ever published.
    clean = [g for g in out if not FORBIDDEN.search(json.dumps(g, ensure_ascii=False))]
    if len(clean) < len(out):
        print(f"left out {len(out) - len(clean)} games with a file or NFO address in their data", flush=True)
    out = clean
    out.sort(key=lambda g: -g["updated"])

    cutoff = time.time() - RECENT_DAYS * 86400
    recent = [g for g in out if g["updated"] >= cutoff]
    older = [g for g in out if g["updated"] < cutoff]
    for g in older:  # the archive is loaded whole: keep it small
        for t in g["timeline"]:
            t.pop("text", None)
    built = int(time.time())
    write_json(PUBLIC / "games.json", {"built": built, "games": recent, "archive": len(older)})
    write_json(PUBLIC / "archive.json", {"built": built, "games": older})
    print(f"{datetime.now():%Y-%m-%d %H:%M} built {len(recent)} recent + {len(older)} archived games "
          f"from {len(posts)} posts ({steam.calls} Steam calls, pending {steam.pending} )", flush=True)


class Lock:
    """One build at a time: the 2-hourly update and the backfill share the Steam cache."""

    def __enter__(self):
        for _ in range(90):  # wait up to 15 minutes
            try:
                os.close(os.open(LOCK, os.O_CREAT | os.O_EXCL))
                return self
            except FileExistsError:
                if time.time() - LOCK.stat().st_mtime > 45 * 60:  # left behind by a crash
                    LOCK.unlink(missing_ok=True)
                    continue
                time.sleep(10)
        raise SystemExit("another build is still running; skipped")

    def __exit__(self, *exc):
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    minutes = 5
    if "--steam-minutes" in sys.argv:
        minutes = float(sys.argv[sys.argv.index("--steam-minutes") + 1])
    with Lock():
        build(minutes)
