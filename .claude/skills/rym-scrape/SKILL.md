---
name: rym-scrape
description: Runs and babysits the RateYourMusic chart+review scraper (scraping/scrape_rym_charts.py, scraping/scrape_reviews.py) to completion across a multi-day run. Handles the relaunch-on-external-kill loop, verifies real DB progress instead of trusting process status, and detects/escalates genuine site-wide rate-limit blocks vs routine self-healing cooldowns. Use whenever asked to run, resume, or speed up this scraper on any machine cloned from this repo.
---

# RYM scraper operator runbook

This scraper takes **days** of wall-clock time for a full genre chart (a
psychedelia-sized chart is 5000+ albums, some with 1000+ reviews each). The
single biggest lever for going fast is minimizing dead time between an
external kill and the next relaunch - treat this as a tight loop for the
rest of the session, not an occasional check-in.

Run the chart scrape first if `albums` is empty, then the review scrape:
```
cd scraping
source ../.venv/bin/activate
python scrape_rym_charts.py    # only needed once, chart -> albums/artists/genres
```
Everything below applies to `scrape_reviews.py`, which is the long-running part.

## The loop (repeat for the rest of the session)

1. **Launch via a tracked background task, never detached.**
   ```
   cd scraping
   source ../.venv/bin/activate && python scrape_reviews.py > /tmp/reviews.log 2>&1
   ```
   Run this with whatever background-bash mechanism gives you a task id and
   delivers a notification when the process stops - **not** `&` / `disown` /
   `nohup`. A detached process still runs, but you silently stop getting kill
   notifications, which is the single biggest cause of the loop stalling for
   hours unnoticed. If you ever catch yourself having used `&`/`disown`, kill
   that process and relaunch it properly tracked immediately.

2. **On every "killed"/"failed" notification for that task, relaunch
   immediately** - same command, no delay, no need to investigate first.
   This is *expected* to happen every 20-40 minutes, repeatedly, for the
   entire run (background-task lifetime limits, unrelated to the site).
   Getting notified and sitting on it is where most lost time comes from.

3. **Before relaunching, spend one query verifying real progress - not just
   that the old process died:**
   ```
   python3 -c "
   import sqlite3, config
   conn = sqlite3.connect(config.DB_PATH)
   print('reviews:', conn.execute('SELECT COUNT(*) FROM user_reviews').fetchone()[0])
   print('albums complete:', conn.execute('SELECT COUNT(*) FROM album_details WHERE reviews_complete=1').fetchone()[0])
   "
   ```
   If these numbers moved since the last check, the kill was routine - just
   relaunch. Skipping this check is how a genuine stall (below) goes
   unnoticed for hours while you keep relaunching into the same wall.

## Recognizing a genuine block vs. routine noise

`scrape_reviews.py` already self-heals ordinary rate limits (503, ~11 min
cooldown, capped retries) - you don't need to do anything for those; they
show up in the log as `rate-limited, cooling down` and clear on their own.
Only escalate if:

- The review/album counts above are **completely unchanged** across a
  relaunch, or
- The log shows `giving up after N cooldowns` (the built-in fail-fast -
  fires after ~30-40 min if a block genuinely isn't clearing, not after
  hours).

When that happens, confirm it's a real site-wide block (not a fluke) with a
single lightweight probe before doing anything else:
```
python3 -c "
from scrapling.fetchers import StealthySession
with StealthySession(headless=True, max_pages=1) as s:
    r = s.fetch('https://rateyourmusic.com/', solve_cloudflare=True,
                 network_idle=False, wait_selector='body', timeout=60000)
    print('STATUS:', r.status)
"
```
- `200` -> not actually blocked, something else is wrong - read the log,
  don't just keep relaunching blindly.
- `503` or a harder connection-level error -> genuinely blocked. Tell the
  user plainly and ask them to switch networks (different Wi-Fi, mobile
  hotspot, or a *different* VPN server/ISP - datacenter/VPN IPs have been
  observed getting blocked *harder* than a plain residential IP). Re-run
  this same probe after the switch to confirm `200` before relaunching the
  full scraper - don't assume the switch worked.
- Don't chase this alone by rotating networks repeatedly without the user's
  involvement - rapid IP-hopping in response to a block is exactly the
  pattern bot-detection escalates against, and it also requires the user's
  action (switching their own network) anyway.

## If this instance still feels slower than another one running in parallel

Check, in order:
1. Is the launch actually tracked (step 1) - or did it drift into
   `&`/`disown`/`nohup`? A detached process means no relaunch notifications
   at all, so it just sits dead after the first kill until someone manually
   checks in.
2. Is the health-check (step 3) happening on *every* relaunch, or being
   skipped sometimes? Skipping it is exactly how earlier runs of this
   scraper twice burned 8-12 hours stuck in a silent stall before anyone
   noticed.
3. Is `config.py`'s `GENRE` set correctly for *this* machine's chart? It
   should differ from any other machine running this code in parallel -
   otherwise both machines are fighting over the same `rym_<genre>.db`
   progress and neither looks like it's moving.
4. `git pull` for the latest code - pacing/cooldown constants in
   `scrape_reviews.py` have been tuned based on real incidents; an older
   clone may still have looser/slower settings than what's described here.
