import datetime
import random
import time

from scrapling.fetchers import StealthySession

import db
from config import CHARTS_BASE

SITE_BASE = "https://rateyourmusic.com"
ITEMS_PER_PAGE = 40
ITEM_SELECTOR = "div.page_charts_section_charts_item"
WAIT_SELECTOR = ".page_charts_section_charts_item"
MAX_ATTEMPTS = 2
PAGE_DELAY_RANGE = (8, 18)
RATE_LIMIT_STATUSES = {429, 503}
RATE_LIMIT_COOLDOWN_SECONDS = 11 * 60
MAX_COOLDOWNS = 8


def page_url(page_num: int) -> str:
    return CHARTS_BASE if page_num == 1 else f"{CHARTS_BASE}{page_num}/"


def now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def extract_name(el):
    """RYM wraps localized names in nested spans; names without a romanization
    are flat (no .ui_name_locale_original child), so .text alone misses them."""
    original = el.css(".ui_name_locale_original")
    if original:
        return original[0].text.strip()
    locale = el.css(".ui_name_locale")
    if locale:
        return locale[0].get_all_text().strip()
    return el.get_all_text().strip()


def extract_items(page, page_num: int):
    items = page.css(ITEM_SELECTOR)
    albums = []

    for local_pos, it in enumerate(items, start=1):
        link_el = it.css(".page_charts_section_charts_item_title a.release")
        title = extract_name(link_el[0]) if link_el else None
        href = link_el[0].attrib.get("href") if link_el else None
        url = SITE_BASE + href if href else None

        date_el = it.css(".page_charts_section_charts_item_date span")
        release_date = date_el[0].text.strip() if date_el else None

        artists = []
        for a in it.css(".page_charts_section_charts_item_credited_text a.artist"):
            name = extract_name(a)
            artist_href = a.attrib.get("href")
            artist_url = SITE_BASE + artist_href if artist_href else None
            if name and artist_url:
                artists.append((name, artist_url))

        genres_primary = [g.text.strip() for g in it.css(".page_charts_section_charts_item_genres_primary a.genre")]
        genres_secondary = [g.text.strip() for g in it.css(".page_charts_section_charts_item_genres_secondary a.genre")]

        if not title or not url:
            continue

        albums.append({
            "rank": (page_num - 1) * ITEMS_PER_PAGE + local_pos,
            "title": title,
            "release_date": release_date,
            "url": url,
            "page": page_num,
            "scraped_at": now(),
            "artists": artists,
            "genres_primary": genres_primary,
            "genres_secondary": genres_secondary,
        })

    return albums


def fetch_page(session, page_num: int):
    """Returns (response_or_None, was_rate_limited)."""
    url = page_url(page_num)
    solve_cf = page_num == 1

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
                return resp, False
            print(f"  page {page_num}: got status {resp.status} (attempt {attempt}/{MAX_ATTEMPTS})")
            if resp.status in RATE_LIMIT_STATUSES:
                return None, True
        except Exception as exc:
            print(f"  page {page_num}: attempt {attempt}/{MAX_ATTEMPTS} raised {exc!r}")
        time.sleep(5)

    return None, False


def main():
    conn = db.get_connection()
    cooldowns_used = 0
    page_num = 1

    with StealthySession(headless=True, max_pages=1) as session:
        while True:
            if db.page_already_scraped(conn, page_num):
                print(f"page {page_num}: already scraped, skipping")
                page_num += 1
                continue

            resp, rate_limited = fetch_page(session, page_num)

            if resp is None:
                if rate_limited:
                    cooldowns_used += 1
                    if cooldowns_used > MAX_COOLDOWNS:
                        print(f"page {page_num}: hit rate limit again after "
                              f"{MAX_COOLDOWNS} cooldowns. Giving up - re-run later to resume.")
                        break
                    print(f"page {page_num}: rate-limited, cooling down for "
                          f"{RATE_LIMIT_COOLDOWN_SECONDS}s ({cooldowns_used}/{MAX_COOLDOWNS})")
                    time.sleep(RATE_LIMIT_COOLDOWN_SECONDS)
                    continue  # retry same page_num
                print(f"page {page_num}: FAILED (non-rate-limit), skipping")
                page_num += 1
                continue

            albums = extract_items(resp, page_num)
            if not albums:
                print(f"page {page_num}: no albums extracted - reached end of chart, stopping")
                break

            for album in albums:
                db.save_album(conn, album)
            db.mark_page_scraped(conn, page_num, len(albums), now())
            conn.commit()

            print(f"page {page_num}: saved {len(albums)} albums")

            page_num += 1
            time.sleep(random.uniform(*PAGE_DELAY_RANGE))

    conn.close()
    print("done")


if __name__ == "__main__":
    main()
