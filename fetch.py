"""Fetch new r/CrackWatch posts from Reddit's public RSS feed.

No login and no account: the same feed any news reader uses, asked once per
run. The feed has no post flair, so the kind of post is worked out from its
title and text (see flair_of). Posts are merged into data/raw/posts.json by id,
which builder/build.py turns into the site.

    python fetch.py
"""
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "data" / "raw" / "posts.json"
FEED = "https://www.reddit.com/r/CrackWatch/new/.rss?limit=100"
USER_AGENT = "crackwatch-plainly/1.0 (news reader; one request every 2 hours)"
NS = {"a": "http://www.w3.org/2005/Atom"}


def to_markdown(content):
    """Reddit's HTML back to the markdown the builder reads: tables as | rows |, links as [text](url)."""
    s = html.unescape(content or "")
    s = re.sub(r'<a href="([^"]+)"[^>]*>(.*?)</a>', r"[\2](\1)", s, flags=re.S)
    s = re.sub(r"<tr>\s*", "\n| ", s)
    s = re.sub(r"\s*</t[hd]>\s*", " | ", s)
    s = re.sub(r"<(strong|b)>(.*?)</\1>", r"**\2**", s, flags=re.S)
    s = re.sub(r"<li>", "\n* ", s)
    s = re.sub(r"<br\s*/?>|</p>|</tr>|</table>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)  # entities inside the converted text
    s = s.replace("[link]", "").replace("[comments]", "")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", s)).strip()


def flair_of(title, body):
    """The subreddit's post label, which the RSS feed leaves out."""
    if re.match(r"^Daily Releases?\b", title, re.I):
        return "Daily release"
    if re.search(r"\b(hypervisor|hvisor)\b", title, re.I):
        return "Denuvo Hypervisor Workaround"
    if re.match(r"^Denuvo removed from\b", title, re.I):
        return "Article/News"
    # Scene-style name ("Game.Name-GROUP"), or any post that links its release on predb.
    scene = re.match(r"^[^\s]+-([\w.]+)$", title)
    if scene or re.search(r"predb\.(net/rls|club/release)/", body):
        # A "[Denuvo Removed](steamdb…)" link means the publisher took it out first:
        # an ordinary crack. These crackers only ever release Denuvo cracks.
        text = re.sub(r"\[\**Denuvo Removed\**\]\([^)]*\)", "", body, flags=re.I)
        stripped = re.search(r"(denuvo\b.{0,60}\b(removed|stripped)|\b(removed|stripped)\b.{0,60}denuvo)", text, re.I | re.S)
        by_denuvo_cracker = scene and scene.group(1).lower() in DENUVO_CRACKERS
        return "Denuvo release" if stripped or by_denuvo_cracker else "Release"
    return None


DENUVO_CRACKERS = {"voices38", "0xzeon", "empress", "denuvowo"}


def main():
    req = urllib.request.Request(FEED, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        root = ET.fromstring(r.read())
    posts = json.loads(RAW.read_text(encoding="utf-8")) if RAW.exists() else {}
    added = 0
    for e in root.findall("a:entry", NS):
        pid = (e.findtext("a:id", "", NS) or "").removeprefix("t3_")
        if not pid or pid in posts:
            continue
        title = html.unescape(e.findtext("a:title", "", NS))
        body = to_markdown(e.findtext("a:content", "", NS))
        link = e.find("a:link", NS).get("href", "")
        when = e.findtext("a:published", "", NS) or e.findtext("a:updated", "", NS)
        posts[pid] = {
            "id": pid, "title": title, "selftext": body, "link_flair_text": flair_of(title, body),
            "created_utc": int(datetime.fromisoformat(when).timestamp()),
            "permalink": link.removeprefix("https://www.reddit.com"),
        }
        added += 1
    RAW.parent.mkdir(parents=True, exist_ok=True)
    tmp = RAW.with_suffix(".tmp")
    tmp.write_text(json.dumps(posts, ensure_ascii=False), encoding="utf-8")
    tmp.replace(RAW)
    print(f"{datetime.now():%Y-%m-%d %H:%M} r/CrackWatch: {added} new, {len(posts)} total", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.exit(f"{datetime.now():%Y-%m-%d %H:%M} fetch failed: {type(e).__name__}: {e}")
