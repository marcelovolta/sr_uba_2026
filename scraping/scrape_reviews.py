import datetime
import random
import time

from scrapling.fetchers import StealthySession

import db
from config import GENRE

SITE_BASE = "https://rateyourmusic.com"
WAIT_SELECTOR = "body"
MAX_ATTEMPTS = 2
PAGE_DELAY_RANGE = (15, 30)
RATE_LIMIT_STATUSES = {429, 503}
RATE_LIMIT_COOLDOWN_SECONDS = 11 * 60
MAX_COOLDOWNS = 3


def now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def review_page_url(release_url: str, page: int) -> str:
    if page == 1:
        return release_url
    return f"{release_url.rstrip('/')}/reviews/{page}/"


def extract_language(page):
    for th in page.css("th.info_hdr"):
        if "Language" in th.text:
            td = th.parent.css("td")
            if td:
                return td[0].text.strip()
    return None


def extract_review_count(page):
    el = page.css('[itemprop="reviewCount"]')
    if el:
        val = el[0].attrib.get("content")
        if val and val.isdigit():
            return int(val)
    return None


def extract_reviews(page):
    reviews = []
    for r in page.css('div.review[itemtype="http://schema.org/Review"]'):
        user_el = r.css(".review_user a.user")
        if not user_el:
            continue
        username = user_el[0].text.strip()
        user_href = user_el[0].attrib.get("href")
        user_url = SITE_BASE + user_href if user_href else None

        date_el = r.css(".review_date")
        review_date = date_el[0].attrib.get("content") if date_el else None

        link_el = r.css(".review_date a")
        review_href = link_el[0].attrib.get("href") if link_el else None
        review_url = SITE_BASE + review_href if review_href else None

        rating_el = r.css('[itemprop="ratingValue"]')
        rating = None
        if rating_el:
            val = rating_el[0].attrib.get("content")
            if val:
                try:
                    rating = float(val)
                except ValueError:
                    rating = None

        if username and user_url and review_url:
            reviews.append({
                "username": username,
                "user_url": user_url,
                "review_date": review_date,
                "rating": rating,
                "review_url": review_url,
            })
    return reviews


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


def process_album(conn, session, state, album_id, rank, title, release_url):
    """Returns True if the album's reviews are fully scraped (or already were),
    False if the run should abort due to repeated rate limiting."""
    if db.album_reviews_complete(conn, album_id):
        return True

    page_num = 1
    while db.review_page_already_scraped(conn, album_id, page_num):
        page_num += 1

    cooldowns_used = 0

    while True:
        url = review_page_url(release_url, page_num)
        resp, reason = fetch(session, url, state)

        if resp is None:
            if reason == "rate_limited":
                cooldowns_used += 1
                if cooldowns_used > MAX_COOLDOWNS:
                    print(f"album {rank} page {page_num}: giving up after {MAX_COOLDOWNS} cooldowns")
                    return False
                print(f"album {rank} page {page_num}: rate-limited, cooling down for "
                      f"{RATE_LIMIT_COOLDOWN_SECONDS}s ({cooldowns_used}/{MAX_COOLDOWNS})")
                time.sleep(RATE_LIMIT_COOLDOWN_SECONDS)
                continue
            if reason == "cf_blocked":
                print(f"album {rank} page {page_num}: cloudflare re-challenge needed, retrying")
                time.sleep(5)
                continue
            print(f"album {rank} page {page_num}: FAILED (non-rate-limit), skipping this page")
            page_num += 1
            time.sleep(10)
            continue

        if page_num == 1:
            language = extract_language(resp)
            review_count = extract_review_count(resp)
            if language:
                db.set_album_language(conn, album_id, language)
            db.mark_album_detail(conn, album_id, review_count, now())

        reviews = extract_reviews(resp)
        for rv in reviews:
            db.save_review(conn, rv["username"], rv["user_url"], album_id,
                            rv["review_date"], rv["rating"], rv["review_url"])
        db.mark_review_page_scraped(conn, album_id, page_num, len(reviews), now())
        conn.commit()

        print(f"album {rank} '{title}': page {page_num} -> {len(reviews)} reviews")

        if len(reviews) == 0:
            db.mark_album_reviews_complete(conn, album_id)
            return True

        page_num += 1
        time.sleep(random.uniform(*PAGE_DELAY_RANGE))


def main():
    conn = db.get_connection()
    albums = conn.execute(
        "SELECT id, rank, title, url FROM albums WHERE genre = ? ORDER BY rank", (GENRE,)
    ).fetchall()
    state = {"solved": False}

    with StealthySession(headless=True, max_pages=1) as session:
        for album_id, rank, title, url in albums:
            if db.album_reviews_complete(conn, album_id):
                continue
            ok = process_album(conn, session, state, album_id, rank, title, url)
            if not ok:
                print("aborting run due to repeated rate limiting - re-run later to resume")
                break

    conn.close()
    print("done")


if __name__ == "__main__":
    main()
