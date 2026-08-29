"""Merge another genre's database into this one (DB_PATH from config.py).

Usage: python merge_dbs.py path/to/other_genre.db

Two DB files that both started life as a copy of the same export will have
their own independently-assigned autoincrement ids for anything each side
discovered on its own afterward - the same integer id can mean two totally
different albums/artists/genres in each file. So rows are matched by their
real-world natural key (url for albums/artists, name for genres, username
for users, review_url for reviews), never by id, and every foreign key gets
remapped from the source file's id space into this DB's id space as it's
copied over.

scraped_pages is intentionally NOT merged - it's chart-pagination
bookkeeping specific to each genre's own scrape run, meaningless once
scraping is done and each side's chart is fully discovered.
"""
import sys

import db


def merge(conn, source_path):
    conn.execute(f"ATTACH DATABASE '{source_path}' AS src")
    conn.execute("PRAGMA foreign_keys = OFF")
    stats = {}

    # --- albums: match by url, remap id ---
    album_id_map = {}
    src_rows = conn.execute(
        "SELECT id, genre, rank, title, release_date, url, language, page, scraped_at FROM src.albums"
    ).fetchall()
    inserted = 0
    for src_id, genre, rank, title, release_date, url, language, page, scraped_at in src_rows:
        row = conn.execute("SELECT id FROM albums WHERE url = ?", (url,)).fetchone()
        if row:
            album_id_map[src_id] = row[0]
        else:
            cur = conn.execute(
                """INSERT INTO albums (genre, rank, title, release_date, url, language, page, scraped_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (genre, rank, title, release_date, url, language, page, scraped_at),
            )
            album_id_map[src_id] = cur.lastrowid
            inserted += 1
    stats["albums"] = f"{inserted} new / {len(src_rows)} total from source"

    # --- artists: match by url, remap id ---
    artist_id_map = {}
    src_rows = conn.execute("SELECT id, name, url FROM src.artists").fetchall()
    inserted = 0
    for src_id, name, url in src_rows:
        row = conn.execute("SELECT id FROM artists WHERE url = ?", (url,)).fetchone()
        if row:
            artist_id_map[src_id] = row[0]
        else:
            cur = conn.execute("INSERT INTO artists (name, url) VALUES (?, ?)", (name, url))
            artist_id_map[src_id] = cur.lastrowid
            inserted += 1
    stats["artists"] = f"{inserted} new / {len(src_rows)} total from source"

    # --- genres: match by name, remap id ---
    genre_id_map = {}
    src_rows = conn.execute("SELECT id, name FROM src.genres").fetchall()
    inserted = 0
    for src_id, name in src_rows:
        row = conn.execute("SELECT id FROM genres WHERE name = ?", (name,)).fetchone()
        if row:
            genre_id_map[src_id] = row[0]
        else:
            cur = conn.execute("INSERT INTO genres (name) VALUES (?)", (name,))
            genre_id_map[src_id] = cur.lastrowid
            inserted += 1
    stats["genres"] = f"{inserted} new / {len(src_rows)} total from source"

    # --- users: username is already a stable natural PK, no id remap needed ---
    src_rows = conn.execute(
        "SELECT username, url, age, gender, ratings, signup_date FROM src.users"
    ).fetchall()
    inserted = 0
    for username, url, age, gender, ratings, signup_date in src_rows:
        cur = conn.execute(
            """INSERT OR IGNORE INTO users (username, url, age, gender, ratings, signup_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (username, url, age, gender, ratings, signup_date),
        )
        inserted += cur.rowcount
    stats["users"] = f"{inserted} new / {len(src_rows)} total from source"

    # --- album_artists: remap both fks ---
    src_rows = conn.execute("SELECT album_id, artist_id, position FROM src.album_artists").fetchall()
    inserted = 0
    for src_album_id, src_artist_id, position in src_rows:
        cur = conn.execute(
            "INSERT OR IGNORE INTO album_artists (album_id, artist_id, position) VALUES (?, ?, ?)",
            (album_id_map[src_album_id], artist_id_map[src_artist_id], position),
        )
        inserted += cur.rowcount
    stats["album_artists"] = f"{inserted} new / {len(src_rows)} total from source"

    # --- album_genres: remap both fks ---
    src_rows = conn.execute("SELECT album_id, genre_id, kind FROM src.album_genres").fetchall()
    inserted = 0
    for src_album_id, src_genre_id, kind in src_rows:
        cur = conn.execute(
            "INSERT OR IGNORE INTO album_genres (album_id, genre_id, kind) VALUES (?, ?, ?)",
            (album_id_map[src_album_id], genre_id_map[src_genre_id], kind),
        )
        inserted += cur.rowcount
    stats["album_genres"] = f"{inserted} new / {len(src_rows)} total from source"

    # --- user_reviews: remap album_id, dedupe by review_url ---
    src_rows = conn.execute(
        "SELECT username, album_id, review_date, rating, review_url FROM src.user_reviews"
    ).fetchall()
    inserted = 0
    for username, src_album_id, review_date, rating, review_url in src_rows:
        cur = conn.execute(
            """INSERT OR IGNORE INTO user_reviews (username, album_id, review_date, rating, review_url)
               VALUES (?, ?, ?, ?, ?)""",
            (username, album_id_map[src_album_id], review_date, rating, review_url),
        )
        inserted += cur.rowcount
    stats["user_reviews"] = f"{inserted} new / {len(src_rows)} total from source"

    # --- album_details: remap album_id; if both sides have a record, prefer the complete one ---
    src_rows = conn.execute(
        "SELECT album_id, review_count, reviews_complete, scraped_at FROM src.album_details"
    ).fetchall()
    inserted = updated = 0
    for src_album_id, review_count, reviews_complete, scraped_at in src_rows:
        tgt_album_id = album_id_map[src_album_id]
        existing = conn.execute(
            "SELECT reviews_complete FROM album_details WHERE album_id = ?", (tgt_album_id,)
        ).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO album_details (album_id, review_count, reviews_complete, scraped_at)
                   VALUES (?, ?, ?, ?)""",
                (tgt_album_id, review_count, reviews_complete, scraped_at),
            )
            inserted += 1
        elif existing[0] == 0 and reviews_complete == 1:
            conn.execute(
                "UPDATE album_details SET review_count=?, reviews_complete=?, scraped_at=? WHERE album_id=?",
                (review_count, reviews_complete, scraped_at, tgt_album_id),
            )
            updated += 1
    stats["album_details"] = f"{inserted} new, {updated} upgraded to complete / {len(src_rows)} total from source"

    # --- scraped_review_pages: remap album_id ---
    src_rows = conn.execute(
        "SELECT album_id, page, item_count, scraped_at FROM src.scraped_review_pages"
    ).fetchall()
    inserted = 0
    for src_album_id, page, item_count, scraped_at in src_rows:
        cur = conn.execute(
            """INSERT OR IGNORE INTO scraped_review_pages (album_id, page, item_count, scraped_at)
               VALUES (?, ?, ?, ?)""",
            (album_id_map[src_album_id], page, item_count, scraped_at),
        )
        inserted += cur.rowcount
    stats["scraped_review_pages"] = f"{inserted} new / {len(src_rows)} total from source"

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DETACH DATABASE src")
    return stats


def main():
    if len(sys.argv) != 2:
        print("usage: python merge_dbs.py path/to/other_genre.db")
        sys.exit(1)

    conn = db.get_connection()
    stats = merge(conn, sys.argv[1])
    for table, msg in stats.items():
        print(f"{table}: {msg}")

    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    print("foreign_key_check:", "clean" if not issues else issues)
    conn.close()


if __name__ == "__main__":
    main()
