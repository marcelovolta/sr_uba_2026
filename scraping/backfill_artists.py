import time

from scrapling.fetchers import StealthySession

import db
from scrape_rym_charts import extract_items, fetch_page, PAGE_DELAY_RANGE
from config import GENRE
import random


def pages_needing_backfill(conn):
    rows = conn.execute("""
        SELECT DISTINCT a.page FROM albums a
        WHERE a.genre = ?
          AND NOT EXISTS (SELECT 1 FROM album_artists aa WHERE aa.album_id = a.id)
        ORDER BY a.page
    """, (GENRE,)).fetchall()
    return [r[0] for r in rows]


def main():
    conn = db.get_connection()
    cooldowns_used = 0
    max_cooldowns = 10
    rate_limit_cooldown = 11 * 60

    pages = pages_needing_backfill(conn)
    print(f"{len(pages)} pages to reprocess")

    with StealthySession(headless=True, max_pages=1) as session:
        i = 0
        while i < len(pages):
            page_num = pages[i]
            print(f"backfilling page {page_num} ({i + 1}/{len(pages)})")

            resp, rate_limited = fetch_page(session, page_num)

            if resp is None:
                if rate_limited:
                    cooldowns_used += 1
                    if cooldowns_used > max_cooldowns:
                        print(f"hit rate limit again after {max_cooldowns} cooldowns, giving up for now")
                        break
                    print(f"rate-limited, cooling down for {rate_limit_cooldown}s "
                          f"({cooldowns_used}/{max_cooldowns})")
                    time.sleep(rate_limit_cooldown)
                    continue  # retry same page, don't advance i
                print(f"page {page_num}: FAILED (non-rate-limit), moving on")
                i += 1
                continue

            albums = extract_items(resp, page_num)
            fixed = 0
            for album in albums:
                db.save_album(conn, album)
                if album["artists"]:
                    fixed += 1
            conn.commit()
            print(f"page {page_num}: reprocessed {len(albums)} albums, {fixed} had artist data")

            i += 1
            time.sleep(random.uniform(*PAGE_DELAY_RANGE))

    remaining = pages_needing_backfill(conn)
    print(f"backfill done, {len(remaining)} pages still missing some artist data: {remaining}")


if __name__ == "__main__":
    main()
