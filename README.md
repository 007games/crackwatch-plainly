# Crack Watch, plainly

Which PC games got cracked recently, explained without the jargon. A plain-English
reading of the public posts in r/CrackWatch, with game details from Steam.

**News only.** The site reports which games were cracked. It never hosts or links to
downloads; NFO and download links in posts are dropped when the site is built.

## How it works

```
update.cmd  (Windows Task Scheduler, every 2 hours)
  1. fetch.py          r/CrackWatch's public RSS feed -> data/raw/posts.json   (no login)
  2. builder/build.py  posts -> plain-English cards, Steam covers -> docs/data/games.json
  3. git push          docs/ -> GitHub Pages
```

`data/` (raw posts, Steam cache, logs) stays on the PC and is not in the repo.

The site has two data files: `games.json` (games with news in the last 120 days,
loaded first) and `archive.json` (everything older, loaded when someone searches or
opens the Archive tab).

## The archive backfill (one-off)

`backfill.py` adds every post since the subreddit started in 2016. It reads them from
Arctic Shift, a public archive of Reddit data, so no login is needed. The Windows task
"Crack Watch archive backfill" runs it every 30 minutes, and each run works for 20
minutes:

1. **posts**: 100 posts per request, one request every ~15 s, oldest first, into
   `data/raw/archive.json`, until it reaches the posts the RSS fetcher already has
2. **steam**: Steam details for the old games, a slice per run
3. **done**: the task disables itself

Progress is in `data/backfill_state.json` and `data/backfill.log`
(`python backfill.py status`). Run it again from scratch by deleting both files
and `data/raw/archive.json`, then enabling the task.

## Local preview

```
docker compose up -d       http://localhost:8080 (serves docs/)
docker compose down
```

## Run by hand

```
python fetch.py
python builder\build.py
update.cmd                 both, then commit and push if the site changed
```

Both scripts use only the Python standard library.

## What it understands

| Post | Becomes |
| --- | --- |
| `Game.Name-RUNE` | Cracked, by a release group |
| Denuvo stripped, or released by voices38, 0xZeOn, EMPRESS | Denuvo cracked |
| `Daily Releases` tables | one event per row: crack, repack, GOG (DRM-free), update |
| "Hypervisor" in the title or name | Workaround only, with a warning |
| "Denuvo removed from …" | Protection removed (by the publisher) |
| Discussion, humour, announcements | skipped |

The RSS feed has no post flair, so `flair_of()` in `fetch.py` works out the kind of post
from its title and text. Groups and how they are described live in `GROUPS` in
`builder/build.py`; an unknown group is shown as "a release group".

## Credits

Built with [Claude Code](https://claude.com/claude-code), Anthropic's AI coding assistant.
