# RateYourMusic Genre Chart + Review Scraper

Scrapes a RateYourMusic "Top Albums of All Time" genre chart, then every
album's language and full review history (reviewer, star rating, ISO date),
into a normalized SQLite database. Originally built against
`https://rateyourmusic.com/charts/top/album/all-time/g:psychedelia/`
(5040 albums), but the genre is a one-line config change (see below) so the
same code runs unmodified against any other RYM genre chart, including in
parallel on a second machine.

## Files

- `config.py` — the one file to edit per machine/genre: `GENRE` derives both the chart URL and the DB filename
- `db.py` — SQLite schema + helper functions (get-or-create artist/genre/user, save album, save review, track progress)
- `scrape_rym_charts.py` — scrapes the genre chart (album, artist(s), release date, genres) into `albums`/`artists`/`genres`
- `scrape_reviews.py` — for every album, scrapes language + every review (user, ISO date, star rating) into `users`/`user_reviews`
- `backfill_artists.py` — one-off repair for the artist-extraction bug described below; kept as a record of the fix and a template for future backfills
- `merge_dbs.py` — merges another machine's genre DB into this one once both are done scraping (see below)
- `migrate_add_genre.py`, `fix_fk_references.py` — one-time migrations already applied to get the live DB to the current schema (a fresh DB created via `db.get_connection()` gets this schema from the start, so these don't need to run again)
- `rym_psychedelia.db` — the resulting database (gitignored — regenerate via the scripts)

## Running for a different genre / on a second machine

1. `git clone` (or copy) this directory onto the new machine.
2. Set up the environment:
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   scrapling install   # downloads the headless browser (Camoufox/Chrome-for-Testing) scrapling drives
   ```
3. Edit `config.py` — change `GENRE` to the new RYM genre slug (the part after
   `g:` in the chart URL, e.g. `"krautrock"`). That's the only line that needs
   to change; `CHARTS_BASE` and `DB_PATH` derive from it automatically, so the
   new machine writes to its own `rym_<genre>.db` and can run fully in
   parallel without touching the other machine's data.
4. Run the pipeline in order (each step is independently resumable — see
   below — so it's fine to stop and restart at any point):
   ```
   python scrape_rym_charts.py    # chart -> albums/artists/genres
   python scrape_reviews.py       # per-album language + reviews -> users/user_reviews
   ```

### Seeding the second machine from an already-scraped DB (optional but recommended)

An album can legitimately rank on more than one RYM genre chart (a
psych-adjacent album can also chart under punk, say). `albums.genre` only
ever holds *one* genre — whichever chart discovered it first — by design:
its whole job is telling a given machine's `scrape_reviews.py` which albums
are "its own" to fetch reviews for, not describing every genre an album
belongs to (that's what `album_genres`, tied to RYM's own primary/secondary
tag list, is for — that's the table to use for actual genre features in a
recommender). Since `albums.url` is globally unique, if the new machine's own
chart scrape later encounters an album that's already known (e.g. seeded
from another genre's export below), it's automatically left alone — same
`genre`, same `rank`, no duplicate row, and `scrape_reviews.py`'s
genre-filtered query correctly never picks it up as belonging to the new
genre. Verified directly: inserting a duplicate `url` under a different
genre/rank is a no-op (`INSERT OR IGNORE` conflicts on the `url` UNIQUE
constraint), so this needs no extra code.

Seeding isn't required - the new machine's chart scrape works fine against
an empty DB - but it means albums shared between the two genres don't get
their reviews fetched twice. To seed:

```
cp scraping/rym_psychedelia.db /path/to/transfer/rym_export.db
```

On the new machine, after cloning the repo and doing the venv/`scrapling
install` setup above:

```
cd sr_uba_2026/scraping
cp /path/to/transfer/rym_export.db rym_<newgenre>.db   # match config.py's DB_PATH naming
sqlite3 rym_<newgenre>.db "DELETE FROM scraped_pages;"
```

The `DELETE FROM scraped_pages` step is required, not optional:
`scraped_pages` is chart-*pagination* bookkeeping (which page numbers have
been fetched), not genre-scoped. Skip this and the new machine's chart
scraper will see "page 1..N already scraped" left over from psychedelia and
silently skip scraping its own genre's pages entirely.

Then edit `config.py`'s `GENRE` and run `scrape_rym_charts.py` →
`scrape_reviews.py` as normal.

### Merging two genre DBs back together

Once both machines are done, `merge_dbs.py` combines a second genre's DB
into this one:

```
python merge_dbs.py /path/to/other_genre.db
```

It merges every table *except* `scraped_pages` (chart-pagination
bookkeeping, meaningless once a chart is fully scraped - not needed after
merging, and not merged). Everything else - `albums`, `artists`,
`album_artists`, `genres`, `album_genres`, `users`, `user_reviews`,
`album_details`, `scraped_review_pages` - gets merged.

The reason this needs a real script rather than a raw SQL copy: two DB files
that both started as a copy of the same export independently assign their
own autoincrement ids to anything each side discovers afterward, so the same
integer id can mean two completely different albums/artists/genres in each
file. `merge_dbs.py` never trusts ids across files - it matches every row by
its actual real-world identity (`url` for albums/artists, `name` for
genres, `username` for users, `review_url` for reviews) and remaps foreign
keys into the target DB's own id space as each row is copied over. Tested
against a synthetic second-genre DB before relying on it for real: new
albums/artists/reviews merged in cleanly with fresh ids, a genre tag
("Punk Rock") that happened to already exist in the target was correctly
reused rather than duplicated, and every pre-existing shared row was left
completely untouched.

## Running unattended (read this before starting a long run)

`scrape_reviews.py` in particular is not a "run it and check back in an hour"
job — for a psychedelia-sized chart (5000+ albums, some with 1000+ reviews
each) it's realistically **days of wall-clock time**, almost all of it spent
being relaunched over and over. This is normal, not a sign something's wrong.
Operate it like this:

1. **Launch in the background, redirect output to a log file:**
   ```
   python scrape_reviews.py > reviews.log 2>&1 &
   ```
2. **Expect it to die on its own every ~20-40 minutes**, unrelated to RYM
   (background-task lifetime limits, not the site). When it does, don't
   investigate — just relaunch the same command. Do this every time it dies,
   for as long as it takes. Over a multi-day run this can easily mean 100+
   relaunches; that's expected, not a problem to solve.
3. **Before *and* after every relaunch, check real progress, not just that
   the process is running:**
   ```
   python3 -c "
   import sqlite3, config
   conn = sqlite3.connect(config.DB_PATH)
   print('reviews:', conn.execute('SELECT COUNT(*) FROM user_reviews').fetchone()[0])
   print('albums complete:', conn.execute('SELECT COUNT(*) FROM album_details WHERE reviews_complete=1').fetchone()[0])
   "
   ```
   This one habit is what caught two real incidents in the original
   psychedelia run: the review count sat *completely still* for 8-12 hours
   across several relaunches while the process kept "successfully" cooling
   down and retrying forever, because it was hitting something worse than
   the normal rate limit and the old retry cap (`MAX_COOLDOWNS`) was too
   generous to ever give up and surface the problem. If you relaunch blindly
   on every kill notification without checking the count actually moved, you
   can burn most of a day without noticing nothing is happening.
4. **If the count is genuinely stuck (unchanged across a relaunch, or the
   process keeps failing after the current, much smaller `MAX_COOLDOWNS`
   gives up quickly — within ~30-40 min, by design), run this single-request
   probe before doing anything else:**
   ```
   python3 -c "
   from scrapling.fetchers import StealthySession
   with StealthySession(headless=True, max_pages=1) as s:
       r = s.fetch('https://rateyourmusic.com/', solve_cloudflare=True,
                    network_idle=False, wait_selector='body', timeout=60000)
       print('STATUS:', r.status)
   "
   ```
   - `200` → we're not actually blocked; something else is wrong, investigate
     the log.
   - `503` (or a harder connection-level error) → genuinely blocked. Ask
     whoever's at the machine to switch networks (a different Wi-Fi, mobile
     hotspot, or VPN — though VPN/datacenter IPs were observed getting
     blocked *harder*, an outright connection rejection rather than a
     rate-limit 503, so a different ISP/residential IP is the more reliable
     fix). Re-run the probe after the switch to confirm `200` before
     relaunching the full scraper — don't just assume the switch worked.
   - Both times this happened on the original machine, a network switch
     cleared the block within a minute or two; no amount of just waiting
     longer on the same IP was tested to reliably work faster than that.

## Tooling

Fetching uses `scrapling`'s `StealthyFetcher` / `StealthySession`, a Camoufox-based
headless browser with anti-detection patches. RYM sits behind a Cloudflare Turnstile
challenge, so the *first* request in a session passes `solve_cloudflare=True`; the
browser session is then kept alive (`StealthySession(...)` as a context manager) and
reused for every subsequent page, so the Cloudflare cookie only needs to be earned
once per run instead of once per page.

Parsing uses `scrapling.Selector` (a `parsel`/`scrapy`-style CSS/XPath selector) on
the returned page HTML.

## Selectors

### Chart page (`scrape_rym_charts.py`)

Reverse-engineered by saving a chart page's HTML and inspecting it offline:

| Field | Selector (relative to `div.page_charts_section_charts_item`) |
|---|---|
| item container | `div.page_charts_section_charts_item` |
| title | `.page_charts_section_charts_item_title a.release` (name via `extract_name()`, see below) |
| release link | same element, `::attr(href)`, resolved against `https://rateyourmusic.com` |
| release date | `.page_charts_section_charts_item_date span` |
| artist(s) | `.page_charts_section_charts_item_credited_text a.artist` (name via `extract_name()`; multiple for collaborations) |
| genres (primary) | `.page_charts_section_charts_item_genres_primary a.genre` |
| genres (secondary) | `.page_charts_section_charts_item_genres_secondary a.genre` |

Pagination: page 1 is the bare chart URL; page N is `.../g:<genre>/{N}/`.
`scrape_rym_charts.py` doesn't hardcode a total page count — it pages forward
until a page returns zero chart items, which naturally handles genres with a
different number of albums than psychedelia's 126 pages.

### Release page (`scrape_reviews.py`)

| Field | Selector / source |
|---|---|
| language | the row where `th.info_hdr` text contains "Language", sibling `td` text |
| review count | `[itemprop="reviewCount"]::attr(content)` |
| review container | `div.review[itemtype="http://schema.org/Review"]` |
| reviewer username | `.review_user a.user` (name via `extract_name()`; href resolved to profile URL) |
| review date (already ISO!) | `.review_date::attr(content)` — RYM's own `itemprop="datePublished"` is `YYYY-MM-DD`, no parsing needed |
| review permalink | `.review_date a::attr(href)` |
| star rating | `[itemprop="ratingValue"]::attr(content)` — a plain decimal like `"5.00"`, no need to parse the star-image `alt` text |

Review pagination: page 1's reviews are embedded on the release page itself;
page N (N≥2) is `<release_url>reviews/{N}/`. Reviews-per-page isn't a fixed
constant RYM guarantees, so `scrape_reviews.py` pages forward per album until
an empty page, same pattern as the chart scraper.

### `extract_name()` helper (shared by both scripts)

RYM wraps *localized* names (anything with a romanization, e.g. Japanese/Korean
titles) in a nested `<span class="ui_name_locale_original">`, but names that
don't need romanization (mostly Latin-script, e.g. "King Gizzard and The
Lizard Wizard") only get the flat outer `<span class="ui_name_locale">` with
no nested span. `.text` on a Selector element only returns its *direct* text
node, not descendant text, so a naive `.text` fallback silently returns `""`
for the flat case. `extract_name()` tries, in order: `.ui_name_locale_original`
text, then `.ui_name_locale` via `.get_all_text()` (which *does* recurse),
then the element's own `.get_all_text()` as a last resort.

## Database schema

Normalized rather than flat, since artists/genres/users repeat heavily and
downstream work (recommender/feature notebooks elsewhere in this repo) wants
clean joins rather than re-parsing denormalized text:

- `albums` (genre, rank, title, release_date, url, language, page, scraped_at) — `url` globally unique, `(genre, rank)` unique per genre
- `artists` (name, url) — `url` unique
- `album_artists` — junction, keeps credit order via `position`
- `genres` (name) — unique (RYM's own genre tags on each album, not the chart genre)
- `album_genres` — junction, tagged `kind` = `primary` or `secondary` (RYM's own split; a genre can legitimately appear in both for the same album)
- `users` (username as PK, url, age, gender, ratings, signup_date) — `age`/`gender`/`ratings`/`signup_date` are columns reserved for a future pass that visits each user's own profile page; only `username`/`url` are populated by the current scripts
- `user_reviews` (username, album_id, review_date, rating, review_url) — `review_url` unique, drives dedup/resumability
- `scraped_pages` (page, item_count, scraped_at) — chart-scrape resumability
- `album_details` (album_id, review_count, reviews_complete, scraped_at) — tracks whether an album's full review history has been fetched
- `scraped_review_pages` (album_id, page, item_count, scraped_at) — per-album review-page resumability

## Problems hit, and how they were handled

### Chart scraping

1. **Cloudflare block (403) on the very first request.** Fixed by adding
   `solve_cloudflare=True` to the first `fetch()` call, which makes the stealth
   browser detect and click through the Turnstile challenge before continuing.

2. **`network_idle=True` was slow (~30-60s/page)** because RYM keeps background
   network activity (ads, trackers, lazy-loaded widgets) alive indefinitely.
   Switched to `network_idle=False` with a `wait_selector` for the actual
   content, cutting warm-session fetch time to ~4-20s/page.

3. **Site-side rate limiting.** Empirically, RYM allows roughly 14 successful
   requests per ~10-minute window regardless of per-request delay, then serves
   503s. Both scripts detect 429/503 and treat them differently from generic
   failures: sleep `RATE_LIMIT_COOLDOWN_SECONDS` and retry the *same* page/URL
   rather than skipping it, up to a cooldown limit.

4. **Background task lifetime.** Long runs were sometimes externally killed
   after tens of minutes (unrelated to RYM). Every page/review-page is
   committed to SQLite immediately and recorded in a `scraped_*` tracking
   table, so any interrupted run can simply be relaunched and resumes from the
   next unfinished unit of work with no data loss or duplication.

5. **Artist-extraction bug (silently dropped ~230 artists).** See
   `extract_name()` above — the original code's `.text` fallback returned `""`
   for non-localized names instead of raising, so those artists were silently
   omitted rather than erroring. Fixed and verified against saved offline HTML
   before re-running; `backfill_artists.py` reprocessed just the affected
   pages (queried dynamically from the DB) rather than the whole chart.

6. **True "Various Artists" compilations** have no single-artist link in the
   markup at all. Rather than burn more requests chasing an unfixable case,
   these were resolved directly from the release URL slug
   (`/release/album/various-artists/...`) into a synthetic "Various Artists"
   artist row, with no additional site requests.

### Review scraping

7. **A much longer rate-limit than the chart scrape saw.** The chart scrape's
   ~10-minute rolling window didn't hold up under the sustained multi-day
   volume of scraping every review for every album (a single popular album
   like Revolver has ~1,449 reviews ≈ 180+ paginated fetches on its own).
   Twice, the scraper got stuck hitting an immediate 503 for **8-12+ hours
   straight** — worse, the original retry logic (`MAX_COOLDOWNS=300`) just
   kept patiently retrying every ~11 minutes for the entire duration without
   ever giving up, so the stall was silent and only surfaced when reviewing
   progress manually. Fixed two ways: (a) dropped `MAX_COOLDOWNS` to a small
   number (fails fast, in ~30-40 min, instead of silently hanging for half a
   day) and slowed the steady-state per-page delay; (b) when genuinely stuck,
   the fix was switching the network (VPN or alternate ISP) — a completely
   fresh residential IP cleared the block immediately both times, while
   VPN/datacenter IPs were often blocked *harder* (an outright connection
   -level rejection rather than a rate-limit 503), consistent with RYM's
   WAF pre-flagging known VPN/datacenter ranges more aggressively than a
   plain rate limit.
8. **RYM's own markup did the hard parsing for us.** Review dates come with
   `itemprop="datePublished" content="YYYY-MM-DD"` already in ISO 8601, and
   star ratings come with `itemprop="ratingValue" content="5.00"` as a plain
   decimal — no need to parse `"May 22 2012"` text or a `"5.00 stars"` image
   `alt` attribute, both of which were the naive first approach.

## Politeness / pacing choices

- One browser session reused for a whole run instead of relaunching per page
- Backs off based on the site's own rate-limit signal (503/429) rather than a
  guessed fixed rate, and fails fast (rather than silently hanging) when a
  cooldown clearly isn't working
- RateYourMusic's Terms of Service disallow automated scraping; this was run
  for personal/research use. Keep pacing conservative and don't parallelize
  requests within a single machine if extending this further.

## Known caveats

- `release_date` is stored as RYM's raw display text (e.g. `"5 August 1966"`,
  or just a year like `"1971"` for some releases) — not normalized into a
  date type. Parse it downstream if you need real date arithmetic.
- `users.age`, `gender`, `ratings`, `signup_date` are schema-only placeholders
  right now — populating them requires a separate pass that visits each
  unique user's own profile page (`users.url`), which hasn't been built yet.
- A handful of albums have zero genre tags or (for true compilations) a
  synthetic "Various Artists" credit — both are genuine gaps/edge cases in
  RYM's own markup, not extraction bugs.
