import sqlite3

from config import DB_PATH, GENRE

SCHEMA = """
CREATE TABLE IF NOT EXISTS albums (
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
);

CREATE TABLE IF NOT EXISTS artists (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS album_artists (
    album_id  INTEGER NOT NULL REFERENCES albums(id),
    artist_id INTEGER NOT NULL REFERENCES artists(id),
    position  INTEGER NOT NULL,
    PRIMARY KEY (album_id, artist_id)
);

CREATE TABLE IF NOT EXISTS genres (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS album_genres (
    album_id INTEGER NOT NULL REFERENCES albums(id),
    genre_id INTEGER NOT NULL REFERENCES genres(id),
    kind     TEXT NOT NULL CHECK (kind IN ('primary', 'secondary')),
    PRIMARY KEY (album_id, genre_id, kind)
);

CREATE TABLE IF NOT EXISTS scraped_pages (
    page       INTEGER PRIMARY KEY,
    item_count INTEGER NOT NULL,
    scraped_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username    TEXT PRIMARY KEY,
    url         TEXT NOT NULL UNIQUE,
    age         INTEGER,
    gender      TEXT,
    ratings     INTEGER,
    signup_date TEXT
);

CREATE TABLE IF NOT EXISTS user_reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL REFERENCES users(username),
    album_id    INTEGER NOT NULL REFERENCES albums(id),
    review_date TEXT,
    rating      REAL,
    review_url  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS album_details (
    album_id        INTEGER PRIMARY KEY REFERENCES albums(id),
    review_count    INTEGER,
    reviews_complete INTEGER NOT NULL DEFAULT 0,
    scraped_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scraped_review_pages (
    album_id   INTEGER NOT NULL REFERENCES albums(id),
    page       INTEGER NOT NULL,
    item_count INTEGER NOT NULL,
    scraped_at TEXT NOT NULL,
    PRIMARY KEY (album_id, page)
);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(albums)")}
    if "language" not in cols:
        conn.execute("ALTER TABLE albums ADD COLUMN language TEXT")
    if "genre" not in cols:
        conn.execute(f"ALTER TABLE albums ADD COLUMN genre TEXT DEFAULT '{GENRE}'")
    return conn


def get_or_create_artist(conn, name, url):
    row = conn.execute("SELECT id FROM artists WHERE url = ?", (url,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO artists (name, url) VALUES (?, ?)", (name, url))
    return cur.lastrowid


def get_or_create_genre(conn, name):
    row = conn.execute("SELECT id FROM genres WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO genres (name) VALUES (?)", (name,))
    return cur.lastrowid


def page_already_scraped(conn, page):
    row = conn.execute("SELECT 1 FROM scraped_pages WHERE page = ?", (page,)).fetchone()
    return row is not None


def save_album(conn, album):
    """album: dict with rank, title, release_date, url, page, scraped_at, artists, genres_primary, genres_secondary"""
    cur = conn.execute(
        """INSERT OR IGNORE INTO albums (genre, rank, title, release_date, url, page, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (GENRE, album["rank"], album["title"], album["release_date"], album["url"],
         album["page"], album["scraped_at"]),
    )
    if cur.rowcount == 0:
        row = conn.execute("SELECT id FROM albums WHERE url = ?", (album["url"],)).fetchone()
        album_id = row[0]
    else:
        album_id = cur.lastrowid

    for position, (name, url) in enumerate(album["artists"], start=1):
        artist_id = get_or_create_artist(conn, name, url)
        conn.execute(
            "INSERT OR IGNORE INTO album_artists (album_id, artist_id, position) VALUES (?, ?, ?)",
            (album_id, artist_id, position),
        )

    for name in album["genres_primary"]:
        genre_id = get_or_create_genre(conn, name)
        conn.execute(
            "INSERT OR IGNORE INTO album_genres (album_id, genre_id, kind) VALUES (?, ?, 'primary')",
            (album_id, genre_id),
        )

    for name in album["genres_secondary"]:
        genre_id = get_or_create_genre(conn, name)
        conn.execute(
            "INSERT OR IGNORE INTO album_genres (album_id, genre_id, kind) VALUES (?, ?, 'secondary')",
            (album_id, genre_id),
        )

    return album_id


def mark_page_scraped(conn, page, item_count, scraped_at):
    conn.execute(
        "INSERT OR REPLACE INTO scraped_pages (page, item_count, scraped_at) VALUES (?, ?, ?)",
        (page, item_count, scraped_at),
    )


def get_or_create_user(conn, username, url):
    conn.execute("INSERT OR IGNORE INTO users (username, url) VALUES (?, ?)", (username, url))


def save_review(conn, username, user_url, album_id, review_date, rating, review_url):
    get_or_create_user(conn, username, user_url)
    conn.execute(
        """INSERT OR IGNORE INTO user_reviews (username, album_id, review_date, rating, review_url)
           VALUES (?, ?, ?, ?, ?)""",
        (username, album_id, review_date, rating, review_url),
    )


def set_album_language(conn, album_id, language):
    conn.execute("UPDATE albums SET language = ? WHERE id = ?", (language, album_id))


def album_detail_scraped(conn, album_id):
    row = conn.execute("SELECT 1 FROM album_details WHERE album_id = ?", (album_id,)).fetchone()
    return row is not None


def mark_album_detail(conn, album_id, review_count, scraped_at):
    conn.execute(
        """INSERT INTO album_details (album_id, review_count, reviews_complete, scraped_at)
           VALUES (?, ?, 0, ?)
           ON CONFLICT(album_id) DO UPDATE SET review_count = excluded.review_count""",
        (album_id, review_count, scraped_at),
    )


def mark_album_reviews_complete(conn, album_id):
    conn.execute("UPDATE album_details SET reviews_complete = 1 WHERE album_id = ?", (album_id,))


def album_reviews_complete(conn, album_id):
    row = conn.execute(
        "SELECT reviews_complete FROM album_details WHERE album_id = ?", (album_id,)
    ).fetchone()
    return row is not None and row[0] == 1


def review_page_already_scraped(conn, album_id, page):
    row = conn.execute(
        "SELECT 1 FROM scraped_review_pages WHERE album_id = ? AND page = ?", (album_id, page)
    ).fetchone()
    return row is not None


def mark_review_page_scraped(conn, album_id, page, item_count, scraped_at):
    conn.execute(
        """INSERT OR REPLACE INTO scraped_review_pages (album_id, page, item_count, scraped_at)
           VALUES (?, ?, ?, ?)""",
        (album_id, page, item_count, scraped_at),
    )
