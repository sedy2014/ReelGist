import os
import sqlite3

import database


def save(url, **kw):
    args = dict(title="t", uploader="u", thumbnail="", duration=1,
                transcript="tx", segments=[{"start": 0, "end": 1, "text": "a"}], summary="s")
    args.update(kw)
    database.save_reel(url, **args)


def test_upsert_preserves_id(db_path):
    url = "https://www.instagram.com/reel/AAAAAAAAAAA/"
    save(url, title="first")
    first = database.get_reel_by_url(url)
    save(url, title="second")
    second = database.get_reel_by_url(url)
    assert second["id"] == first["id"]
    assert second["title"] == "second"
    assert len(database.get_all_reels()) == 1


def test_shortcode_lookup_and_truncated_roundtrip(db_path):
    save("https://www.instagram.com/reel/AAAAAAAAAAA/", truncated=True)
    row = database.get_reel_by_shortcode("AAAAAAAAAAA")
    assert row and row["truncated"] is True
    assert row["segments"] == [{"start": 0, "end": 1, "text": "a"}]
    assert database.get_reel_by_shortcode("BBBBBBBBBBB") is None


def test_card_and_translation_roundtrip(db_path):
    card = {"tldr": "x", "steps": [{"text": "s", "evidence": "e", "t": 1.0, "verified": True}],
            "tools": [], "snippets": [], "insights": [], "warnings": [], "missing": ["m"]}
    save("https://www.instagram.com/reel/AAAAAAAAAAA/", card=card, language="hindi",
         transcript_en="hello", segments_en=[{"start": 0, "end": 1, "text": "hello"}])
    row = database.get_reel_by_shortcode("AAAAAAAAAAA")
    assert row["card"] == card and row["language"] == "hindi"
    assert row["transcript_en"] == "hello" and row["segments_en"][0]["text"] == "hello"


def test_reel_without_card_reads_as_none(db_path):
    save("https://www.instagram.com/reel/AAAAAAAAAAA/")
    row = database.get_reel_by_shortcode("AAAAAAAAAAA")
    assert row["card"] is None and row["segments_en"] == [] and row["language"] is None


def test_connections_are_closed(db_path):
    save("https://www.instagram.com/reel/AAAAAAAAAAA/")
    database.get_all_reels()
    database.get_reel_by_url("x")
    database.delete_reel(1)
    os.remove(db_path)  # fails on Windows if any connection is still open


def test_migrates_old_schema(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE reels (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE NOT NULL, "
                  "title TEXT, uploader TEXT, thumbnail TEXT, duration INTEGER DEFAULT 0, transcript TEXT, "
                  "segments TEXT, summary TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("INSERT INTO reels (url, title) VALUES ('https://www.instagram.com/reel/OLDCODE1234/?xtok=1', 'old')")
    c.close()
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    row = database.get_reel_by_shortcode("OLDCODE1234")
    assert row["title"] == "old" and row["truncated"] is False
    assert row["card"] is None and row["language"] is None  # new columns added by migration
