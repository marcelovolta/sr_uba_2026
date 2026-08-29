"""One-time migration: adds albums.genre and switches the UNIQUE constraint
from rank alone to (genre, rank), so multiple genre-charts can eventually
share one DB (or be merged) without rank collisions. SQLite can't add a
table-level constraint via ALTER TABLE, so this rebuilds the table.

Run once against an existing DB that predates the genre column. Safe to run
against a fresh DB too (it's a no-op if albums.genre already exists).
"""
import sqlite3

from config import DB_PATH, GENRE


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    cols = {row[1] for row in conn.execute("PRAGMA table_info(albums)")}
    if "genre" in cols:
        print("albums.genre already exists, nothing to do")
        conn.close()
        return

    old_count = conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
    print(f"migrating {old_count} albums to genre='{GENRE}'...")

    conn.execute("ALTER TABLE albums RENAME TO albums_old")

    conn.execute("""
        CREATE TABLE albums (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            genre        TEXT NOT NULL,
            rank         INTEGER NOT NULL,
            title        TEXT NOT NULL,
            release_date TEXT,
            url          TEXT NOT NULL UNIQUE,
            language     TEXT,
            page         INTEGER NOT NULL,
            scraped_at   TEXT NOT NULL,
            UNIQUE (genre, rank)
        )
    """)

    conn.execute("""
        INSERT INTO albums (id, genre, rank, title, release_date, url, language, page, scraped_at)
        SELECT id, ?, rank, title, release_date, url, language, page, scraped_at
        FROM albums_old
    """, (GENRE,))

    new_count = conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
    if new_count != old_count:
        conn.rollback()
        raise RuntimeError(f"row count mismatch: {old_count} old vs {new_count} new, rolled back")

    conn.execute("DROP TABLE albums_old")
    conn.commit()

    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    if issues:
        print("WARNING: foreign_key_check found issues:", issues)
    else:
        print("foreign_key_check: clean")

    print(f"migration complete: {new_count} albums now tagged genre='{GENRE}'")
    conn.close()


if __name__ == "__main__":
    main()
