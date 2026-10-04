"""One-off watcher: add every old r/CrackWatch post (back to 2016), slowly, then stop.

Run by the Windows task "Crack Watch archive backfill" every 30 minutes. Each
run works for at most RUN_MINUTES and keeps its place in data/backfill_state.json,
so a shutdown or reboot loses nothing.

  phase "posts"  the Arctic Shift archive (public Reddit data for researchers,
                 no login), 100 posts per request, one request every ~15 s,
                 oldest first, into data/raw/archive.json
  phase "steam"  the builder fills in Steam details for the old games, a slice
                 per run, until nothing is left to look up
  phase "done"   the task disables itself

    python backfill.py          one run (what the task does)
    python backfill.py status   where it is
"""
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import fetch  # flair_of: the post label when the archive has none

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ARCHIVE = DATA / "raw" / "archive.json"
LIVE = DATA / "raw" / "posts.json"
STATE = DATA / "backfill_state.json"
TASK = "Crack Watch archive backfill"
RUN_MINUTES = float(os.environ.get("BACKFILL_MINUTES", 20))
START = int(datetime(2016, 9, 1, tzinfo=timezone.utc).timestamp())
API = ("https://arctic-shift.photon-reddit.com/api/posts/search?subreddit=CrackWatch&limit=100&sort=asc"
       "&fields=id,title,link_flair_text,created_utc,selftext,author&after={after}")
USER_AGENT = "crackwatch-plainly/1.0 (one-time archive backfill, one request every 15 s)"

# Old post labels, mapped to the ones the builder knows.
OLD_FLAIRS = {"NFO": "Release", "Repack": "Repack", "New Game Repack": "Repack", "Old Game Repack": "Repack"}


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M} {msg}", flush=True)


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def get_page(after):
    req = urllib.request.Request(API.format(after=after), headers={"User-Agent": USER_AGENT})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())["data"] or []
        except urllib.error.HTTPError as e:
            # 422 comes back now and then when a query times out on the archive's side.
            if e.code not in (422, 429, 500, 502, 503, 504):
                raise
            reason = e.read()[:120].decode("utf-8", "replace")
            wait = int(e.headers.get("X-RateLimit-Reset") or 0) + 60 * (attempt + 1)
            log(f"archive busy ({e.code} {reason}), waiting {wait} s")
            time.sleep(wait)
    raise RuntimeError("archive kept refusing; trying again next run")


def phase_posts(state, deadline):
    posts = load(ARCHIVE, {})
    # Stop where the live RSS fetcher's posts begin.
    live = load(LIVE, {})
    stop_at = min((p["created_utc"] for p in live.values()), default=int(time.time()))
    after = state.get("after", START)
    pages = 0
    while time.time() < deadline:
        page = get_page(after)
        new_after = after
        for p in page:
            created = int(p.get("created_utc") or 0)
            new_after = max(new_after, created)
            if created >= stop_at:
                continue
            title, body = p.get("title") or "", p.get("selftext") or ""
            flair = p.get("link_flair_text")
            flair = OLD_FLAIRS.get(flair, flair) or fetch.flair_of(title, body)
            posts[p["id"]] = {
                "id": p["id"], "title": title, "selftext": "" if body in ("[removed]", "[deleted]") else body,
                "link_flair_text": flair, "created_utc": created, "author": p.get("author"),
                "permalink": f"/r/CrackWatch/comments/{p['id']}/",
            }
        pages += 1
        if not page or new_after >= stop_at or new_after == after:
            state["phase"] = "steam"
            state.pop("after", None)
            break
        after = new_after
        state["after"] = after
        if pages % 5 == 0:  # keep progress even if the run is cut short
            save(ARCHIVE, posts)
            save(STATE, state)
        time.sleep(random.uniform(12, 18))  # the archive asks to slow down below ~8 s
    save(ARCHIVE, posts)
    reached = datetime.fromtimestamp(state.get("after", stop_at), timezone.utc)
    log(f"posts: {pages} pages this run, {len(posts)} archived posts, reached {reached:%Y-%m-%d}")


def phase_steam(state, deadline):
    minutes = max(1, int((deadline - time.time()) / 60))
    out = subprocess.run([sys.executable, "-u", str(HERE / "builder" / "build.py"), "--steam-minutes", str(minutes)],
                         capture_output=True, text=True, encoding="utf-8")
    print(out.stdout.strip(), out.stderr.strip(), sep="\n", flush=True)
    if "pending 0 " in out.stdout:
        state["phase"] = "done"


def finish():
    subprocess.run(["schtasks", "/Change", "/TN", TASK, "/DISABLE"], capture_output=True)
    log("finished: all old posts added and looked up on Steam; task disabled")


def main():
    state = load(STATE, {"phase": "posts"})
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(state), f"archive: {len(load(ARCHIVE, {}))} posts")
        return
    deadline = time.time() + RUN_MINUTES * 60
    if state["phase"] == "posts":
        phase_posts(state, deadline)
    if state["phase"] == "steam" and time.time() < deadline - 60:
        phase_steam(state, deadline)
    save(STATE, state)
    if state["phase"] == "done":
        finish()


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    try:
        main()
    except Exception as e:
        log(f"run failed, will retry next run: {type(e).__name__}: {e}")
        sys.exit(1)
