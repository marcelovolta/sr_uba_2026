import datetime
import random
import time

from scrapling.fetchers import StealthySession

import db
from config import GENRE

WAIT_SELECTOR = "body"
MAX_ATTEMPTS = 2
PAGE_DELAY_RANGE = (8, 18)
RATE_LIMIT_STATUSES = {403, 429, 503}
RATE_LIMIT_COOLDOWN_SECONDS = 11 * 60
MAX_COOLDOWNS = 3


def now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def extract_cover_url(page):
    """Cover art lives in <div class="coverart_<numeric id>"><img src=...>;
    the numeric suffix varies per album so match on the class prefix."""
    el = page.css('div[class^="coverart_"] img')
    if not el:
        return None
    src = el[0].attrib.get("src")
    if not src:
        return None
    return "https:" + src if src.startswith("//") else src


def fetch(session, url, state):
    """Returns (response_or_None, failure_reason). failure_reason in (None, 'rate_limited', 'cf_blocked', 'other')."""
    solve_cf = not state["solved"]

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = session.fetch(
                url,
                solve_cloudflare=solve_cf,
                network_idle=False,
                wait_selector=WAIT_SELECTOR,
                timeout=45000,
            )
            if resp.status == 200:
                state["solved"] = True
                return resp, None
            print(f"  {url}: got status {resp.status} (attempt {attempt}/{MAX_ATTEMPTS})")
            if resp.status == 403:
                state["solved"] = False
                return None, "cf_blocked"
            if resp.status in RATE_LIMIT_STATUSES:
                return None, "rate_limited"
        except Exception as exc:
            print(f"  {url}: attempt {attempt}/{MAX_ATTEMPTS} raised {exc!r}")
        time.sleep(5)

    return None, "other"


def process_album(conn, session, state, rank, title, url):
    """Returns True if handled (cover saved or genuinely absent), False to abort the run."""
    cooldowns_used = 0

    while True:
        resp, reason = fetch(session, url, state)

        if resp is None:
            if reason in ("rate_limited", "cf_blocked"):
                cooldowns_used += 1
                if cooldowns_used > MAX_COOLDOWNS:
                    print(f"album {rank} '{title}': giving up after {MAX_COOLDOWNS} cooldowns ({reason})")
                    return False
                print(f"album {rank} '{title}': {reason}, cooling down for "
                      f"{RATE_LIMIT_COOLDOWN_SECONDS}s ({cooldowns_used}/{MAX_COOLDOWNS})")
                time.sleep(RATE_LIMIT_COOLDOWN_SECONDS)
                continue
            print(f"album {rank} '{title}': FAILED (non-rate-limit), skipping for now")
            return True

        return resp


def main():
    conn = db.get_connection()
    albums = conn.execute(
        "SELECT id, rank, title, url FROM albums WHERE genre = ? AND cover_url IS NULL ORDER BY rank",
        (GENRE,),
    ).fetchall()
    print(f"{len(albums)} albums need cover_url")

    state = {"solved": False}

    with StealthySession(headless=True, max_pages=1) as session:
        for album_id, rank, title, url in albums:
            result = process_album(conn, session, state, rank, title, url)

            if result is False:
                print("aborting run due to repeated rate limiting - re-run later to resume")
                break

            if result is True:
                # non-rate-limit failure on this album's single request - leave cover_url
                # NULL so a later run retries it, and move on to the next album
                time.sleep(random.uniform(*PAGE_DELAY_RANGE))
                continue

            cover_url = extract_cover_url(result)
            # empty string (not NULL) marks "checked, genuinely no cover art found",
            # distinct from NULL ("not attempted yet") so it isn't retried forever
            db.set_album_cover(conn, album_id, cover_url or "")
            conn.commit()
            print(f"album {rank} '{title}': cover -> {cover_url or '(none found)'}")

            time.sleep(random.uniform(*PAGE_DELAY_RANGE))

    conn.close()
    print("done")


if __name__ == "__main__":
    main()
