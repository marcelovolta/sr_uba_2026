import datetime
import html
import random
import time

from scrapling.fetchers import StealthySession

import db
from config import GENRE

WAIT_SELECTOR = "table.album_info"
MAX_ATTEMPTS = 2
PAGE_DELAY_RANGE = (8, 18)
RATE_LIMIT_STATUSES = {403, 429, 503}
RATE_LIMIT_COOLDOWN_SECONDS = 11 * 60
MAX_COOLDOWNS = 3


def now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def row_text(page, label):
    """table.album_info is a flat list of <tr><th class="info_hdr">Label</th><td>...</td></tr>
    rows; get_all_text() flattens whatever nested links/bold tags a field contains
    (e.g. Released's year link) into plain display text."""
    for tr in page.css("table.album_info tr"):
        th = tr.css("th.info_hdr")
        if not th or th[0].get_all_text().strip() != label:
            continue
        td = tr.css("td")
        if td:
            return " ".join(td[0].get_all_text().split())
    return None


def extract_rating(page):
    """RYM Rating is split across three sibling <span> elements in the row itself
    (avg / max / count), but the page also carries it as clean schema.org
    AggregateRating meta tags - same source scrape_reviews.py already trusts for
    per-review ratingValue - so read those instead of reassembling the spans."""
    val_el = page.css('div[itemprop="aggregateRating"] meta[itemprop="ratingValue"]')
    best_el = page.css('div[itemprop="aggregateRating"] meta[itemprop="bestRating"]')
    count_el = page.css('div[itemprop="aggregateRating"] meta[itemprop="ratingCount"]')
    if not val_el or not count_el:
        return None, None, None
    try:
        rating = float(val_el[0].attrib["content"])
        count = int(count_el[0].attrib["content"])
    except (KeyError, ValueError):
        return None, None, None
    best = best_el[0].attrib.get("content", "5.0") if best_el else "5.0"
    return rating, best, count


def extract_genres(page):
    for tr in page.css("tr.release_genres"):
        th = tr.css("th.info_hdr")
        if not th or th[0].get_all_text().strip() != "Genres":
            continue
        primary = [g.text.strip() for g in tr.css(".release_pri_genres a.genre")]
        secondary = [g.text.strip() for g in tr.css(".release_sec_genres a.genre")]
        return primary, secondary
    return [], []


def extract_descriptors(page):
    for tr in page.css("tr.release_descriptors"):
        span = tr.css(".release_pri_descriptors")
        if span:
            return [d.strip() for d in span[0].text.split(",") if d.strip()]
    return []


def extract_cover_url(page):
    """Cover art lives in <div class="coverart_<numeric id>"><img src=...>;
    the numeric suffix varies per album so match on the class prefix. Same
    page as the info table below, so this piggybacks on the fetch this
    script already makes instead of costing a separate request (that's what
    scrape_covers.py used to do, and why it's now only needed as a backfill
    tool for genres scraped before this was merged in)."""
    el = page.css('div[class^="coverart_"] img')
    if not el:
        return None
    src = el[0].attrib.get("src")
    if not src:
        return None
    return "https:" + src if src.startswith("//") else src


def extract_details(page):
    rating, rating_best, rating_count = extract_rating(page)
    genres_primary, genres_secondary = extract_genres(page)
    return {
        "artist": row_text(page, "Artist"),
        "type": row_text(page, "Type"),
        "released": row_text(page, "Released"),
        "recorded": row_text(page, "Recorded"),
        "rym_rating": rating,
        "rym_rating_best": rating_best,
        "rym_rating_count": rating_count,
        "ranked": row_text(page, "Ranked"),
        "genres_primary": genres_primary,
        "genres_secondary": genres_secondary,
        "descriptors": extract_descriptors(page),
        "language": row_text(page, "Language"),
    }


def render_html_table(fields):
    rows = []

    def add(label, value_html):
        if value_html:
            rows.append(f'    <tr><th scope="row">{html.escape(label)}</th><td>{value_html}</td></tr>')

    add("Artist", html.escape(fields["artist"]) if fields.get("artist") else None)
    add("Type", html.escape(fields["type"]) if fields.get("type") else None)
    add("Released", html.escape(fields["released"]) if fields.get("released") else None)
    add("Recorded", html.escape(fields["recorded"]) if fields.get("recorded") else None)

    if fields.get("rym_rating") is not None:
        rating_text = (
            f"{fields['rym_rating']:.2f} / {fields['rym_rating_best']} "
            f"from {fields['rym_rating_count']:,} ratings"
        )
        add("RYM Rating", html.escape(rating_text))

    add("Ranked", html.escape(fields["ranked"]) if fields.get("ranked") else None)

    genre_lines = []
    if fields.get("genres_primary"):
        genre_lines.append(", ".join(html.escape(g) for g in fields["genres_primary"]))
    if fields.get("genres_secondary"):
        genre_lines.append(", ".join(html.escape(g) for g in fields["genres_secondary"]))
    add("Genres", "<br>\n      ".join(genre_lines) if genre_lines else None)

    if fields.get("descriptors"):
        add("Descriptors", ", ".join(html.escape(d) for d in fields["descriptors"]))

    add("Language", html.escape(fields["language"]) if fields.get("language") else None)

    return '<table class="rym-info-table">\n  <tbody>\n' + "\n".join(rows) + "\n  </tbody>\n</table>"


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


def process_album(conn, session, state, album_id, rank, title, url):
    """Returns True if handled (metadata saved or genuinely unreachable), False to abort the run."""
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

        fields = extract_details(resp)
        html_table = render_html_table(fields)
        db.save_album_metadata(conn, album_id, fields, html_table, now())

        cover_url = extract_cover_url(resp)
        # empty string (not NULL) marks "checked, genuinely no cover art found",
        # distinct from NULL ("not attempted yet") so it isn't retried forever
        db.set_album_cover(conn, album_id, cover_url or "")

        conn.commit()
        print(f"album {rank} '{title}': metadata -> type={fields['type']!r} "
              f"rating={fields['rym_rating']!r} ranked={fields['ranked']!r} "
              f"cover={'yes' if cover_url else '(none found)'}")
        return True


def main():
    conn = db.get_connection()
    albums = conn.execute(
        """SELECT albums.id, albums.rank, albums.title, albums.url
           FROM albums
           LEFT JOIN album_metadata ON album_metadata.album_id = albums.id
           WHERE albums.genre = ? AND album_metadata.album_id IS NULL
           ORDER BY albums.rank""",
        (GENRE,),
    ).fetchall()
    print(f"{len(albums)} albums need metadata (cover_url is captured in the same pass)")

    state = {"solved": False}

    with StealthySession(headless=True, max_pages=1) as session:
        for album_id, rank, title, url in albums:
            if db.album_metadata_scraped(conn, album_id):
                continue
            ok = process_album(conn, session, state, album_id, rank, title, url)

            if not ok:
                print("aborting run due to repeated rate limiting - re-run later to resume")
                break

            time.sleep(random.uniform(*PAGE_DELAY_RANGE))

    conn.close()
    print("done")


if __name__ == "__main__":
    main()
