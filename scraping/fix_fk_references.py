"""One-time fixup: migrate_add_genre.py's ALTER TABLE ... RENAME TO albums_old
caused SQLite to auto-rewrite dependent tables' REFERENCES clauses to point at
"albums_old" instead of "albums". The data itself is fine (ids still match
correctly) - only the declared schema is stale. This rebuilds each affected
child table with the reference pointed back at "albums".
"""
import sqlite3

from config import DB_PATH

TABLES = {
    "album_artists": """
        CREATE TABLE album_artists (
            album_id  INTEGER NOT NULL REFERENCES albums(id),
            artist_id INTEGER NOT NULL REFERENCES artists(id),
            position  INTEGER NOT NULL,
            PRIMARY KEY (album_id, artist_id)
        )
    """,
    "album_genres": """
        CREATE TABLE album_genres (
            album_id INTEGER NOT NULL REFERENCES albums(id),
            genre_id INTEGER NOT NULL REFERENCES genres(id),
            kind     TEXT NOT NULL CHECK (kind IN ('primary', 'secondary')),
            PRIMARY KEY (album_id, genre_id, kind)
        )
    """,
    "user_reviews": """
        CREATE TABLE user_reviews (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL REFERENCES users(username),
            album_id    INTEGER NOT NULL REFERENCES albums(id),
            review_date TEXT,
            rating      REAL,
            review_url  TEXT NOT NULL UNIQUE
        )
    """,
    "album_details": """
        CREATE TABLE album_details (
            album_id        INTEGER PRIMARY KEY REFERENCES albums(id),
            review_count    INTEGER,
            reviews_complete INTEGER NOT NULL DEFAULT 0,
            scraped_at      TEXT NOT NULL
        )
    """,
    "scraped_review_pages": """
        CREATE TABLE scraped_review_pages (
            album_id   INTEGER NOT NULL REFERENCES albums(id),
            page       INTEGER NOT NULL,
            item_count INTEGER NOT NULL,
            scraped_at TEXT NOT NULL,
            PRIMARY KEY (album_id, page)
        )
    """,
}


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    for table, create_sql in TABLES.items():
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        if "albums_old" not in sql:
            print(f"{table}: already clean, skipping")
            continue

        old_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
        conn.execute(create_sql)
        conn.execute(f"INSERT INTO {table} SELECT * FROM {table}_old")
        new_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if new_count != old_count:
            conn.rollback()
            raise RuntimeError(f"{table}: row count mismatch {old_count} vs {new_count}, rolled back")
        conn.execute(f"DROP TABLE {table}_old")
        print(f"{table}: rebuilt, {new_count} rows preserved")

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    print("foreign_key_check:", "clean" if not issues else issues)
    conn.close()


if __name__ == "__main__":
    main()
