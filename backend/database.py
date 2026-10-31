"""
database.py — SQLite persistence layer for ReelGist reel library.
"""
import sqlite3
import json
import os
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "insta_read.db")

# Columns added after the first release; init_db() adds any that are missing
_ADDED_COLUMNS = {
    "truncated": "INTEGER DEFAULT 0",
    "card": "TEXT",           # JSON gist card (None for reels saved before cards existed)
    "language": "TEXT",       # language detected by Whisper, e.g. "hindi"
    "transcript_en": "TEXT",  # English translation for non-English reels
    "segments_en": "TEXT",    # JSON segments of the translation
}


@contextmanager
def _db():
    """Open a connection, commit/rollback via the transaction, and always close it.

    (`with sqlite3.connect()` alone only ends the transaction; it leaves the
    connection open, which holds the file handle on Windows.)
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reels (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT    UNIQUE NOT NULL,
                title       TEXT,
                uploader    TEXT,
                thumbnail   TEXT,
                duration    INTEGER DEFAULT 0,
                transcript  TEXT,
                segments    TEXT,   -- JSON array of {start, end, text}
                summary     TEXT,
                truncated   INTEGER DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migrate databases created before these columns existed
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(reels)")}
        for name, ddl in _ADDED_COLUMNS.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE reels ADD COLUMN {name} {ddl}")


def save_reel(url, title, uploader, thumbnail, duration,
              transcript, segments, summary, truncated=False,
              card=None, language=None, transcript_en=None, segments_en=None):
    # Upsert (not INSERT OR REPLACE) so an existing row keeps its id
    with _db() as conn:
        conn.execute("""
            INSERT INTO reels
              (url, title, uploader, thumbnail, duration,
               transcript, segments, summary, truncated,
               card, language, transcript_en, segments_en)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET
              title=excluded.title, uploader=excluded.uploader,
              thumbnail=excluded.thumbnail, duration=excluded.duration,
              transcript=excluded.transcript, segments=excluded.segments,
              summary=excluded.summary, truncated=excluded.truncated,
              card=excluded.card, language=excluded.language,
              transcript_en=excluded.transcript_en, segments_en=excluded.segments_en
        """, (
            url, title, uploader, thumbnail, duration,
            transcript, json.dumps(segments), summary, int(bool(truncated)),
            json.dumps(card) if card else None, language,
            transcript_en, json.dumps(segments_en) if segments_en else None,
        ))


def _json_or(value, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["segments"] = _json_or(d.get("segments"), [])
    d["segments_en"] = _json_or(d.get("segments_en"), [])
    d["card"] = _json_or(d.get("card"), None)
    d["truncated"] = bool(d.get("truncated"))
    return d


def get_all_reels():
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM reels ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_reel_by_url(url: str):
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM reels WHERE url = ?", (url,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_reel_by_shortcode(code: str):
    """Match a reel regardless of tracking params in the stored URL."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM reels WHERE instr(url, ?) > 0", (f"/{code}/",)
        ).fetchone()
    return _row_to_dict(row) if row else None


def delete_reel(reel_id: int):
    with _db() as conn:
        conn.execute("DELETE FROM reels WHERE id = ?", (reel_id,))
