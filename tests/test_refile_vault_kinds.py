"""Tests for re-filing vault entries whose content kind changed after archiving.

A screenshot's folder is chosen at archive time. An article whose alias did not
exist yet lands under Books and stays there, so the repair has to move the PNG,
its sidecar, the note, and the stored vault_root together.
"""
import sqlite3

import pytest

from xteink_service.refile_vault_kinds import apply_moves, plan, unwritable

TITLE = "There-Is-No-Such-Thing-as-Random-Numbers"
DEVICE_PATH = "/Instapaper/There Is No Such Thing as Random Numbers.epub"


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "state.db"
    vault = tmp_path / "vault"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE synced_screenshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_path TEXT, content_hash TEXT,
            book_title TEXT DEFAULT '', sync_date TEXT DEFAULT '',
            vault_png_path TEXT DEFAULT '', vault_root TEXT DEFAULT 'Books'
        );
        CREATE TABLE document_aliases (
            hash TEXT PRIMARY KEY, title TEXT NOT NULL,
            filename TEXT DEFAULT '', resolved_by TEXT DEFAULT 'manual',
            kind TEXT DEFAULT 'book'
        );
        CREATE TABLE device_files (
            hash TEXT PRIMARY KEY, path TEXT NOT NULL,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

    embed = f"{TITLE}/2026-11-08-25.png"
    books = vault / "Books" / TITLE
    books.mkdir(parents=True)
    (books / "2026-11-08-25.png").write_bytes(b"png")
    (books / "2026-11-08-25.json").write_text("{}")
    (vault / "Books" / f"{TITLE}.md").write_text("# note\n")
    return {"db": str(db), "vault": str(vault), "embed": embed}


def _row(env, root="Books"):
    conn = sqlite3.connect(env["db"])
    conn.execute(
        "INSERT INTO synced_screenshots (device_path, content_hash, book_title, "
        "vault_png_path, vault_root) VALUES (?,?,?,?,?)",
        ("/screenshots/x.bmp", "h", TITLE, env["embed"], root),
    )
    conn.commit()
    conn.close()


def _mark_article(env):
    conn = sqlite3.connect(env["db"])
    conn.execute("INSERT INTO device_files (hash, path) VALUES (?,?)", ("h1", DEVICE_PATH))
    conn.commit()
    conn.close()


def test_nothing_to_do_when_the_kind_still_agrees(env):
    _row(env)
    moves, _ = plan(env["db"], env["vault"])
    assert moves == []


def test_an_article_filed_under_books_is_detected(env):
    _row(env)
    _mark_article(env)

    moves, _ = plan(env["db"], env["vault"])

    assert len(moves) == 1
    assert moves[0]["from_root"] == "Books"
    assert moves[0]["to_root"] == "Articles"


def test_apply_moves_png_sidecar_note_and_updates_the_row(env):
    _row(env)
    _mark_article(env)
    moves, _ = plan(env["db"], env["vault"])

    apply_moves(env["db"], env["vault"], moves)

    from pathlib import Path
    v = Path(env["vault"])
    assert (v / "Articles" / TITLE / "2026-11-08-25.png").exists()
    assert (v / "Articles" / TITLE / "2026-11-08-25.json").exists()
    assert (v / "Articles" / f"{TITLE}.md").exists()
    assert not (v / "Books" / TITLE / "2026-11-08-25.png").exists()
    assert not (v / "Books" / f"{TITLE}.md").exists()

    conn = sqlite3.connect(env["db"])
    assert conn.execute("SELECT vault_root FROM synced_screenshots").fetchone()[0] == "Articles"


def test_apply_is_idempotent(env):
    _row(env)
    _mark_article(env)
    moves, _ = plan(env["db"], env["vault"])
    apply_moves(env["db"], env["vault"], moves)

    assert plan(env["db"], env["vault"])[0] == []


def test_an_existing_destination_note_is_never_overwritten(env):
    _row(env)
    _mark_article(env)
    from pathlib import Path
    dest = Path(env["vault"]) / "Articles"
    dest.mkdir(parents=True)
    (dest / f"{TITLE}.md").write_text("# already here\n")

    moves, notes = plan(env["db"], env["vault"])
    apply_moves(env["db"], env["vault"], moves)

    assert (dest / f"{TITLE}.md").read_text() == "# already here\n"
    assert any("already exists" in n for n in notes)


def test_an_unwritable_source_is_reported_before_anything_moves(env):
    """The service writes the vault as root in its container. Moving from the
    host used to copy the file and then fail to unlink it, leaving the PNG in
    both folders."""
    import os
    from pathlib import Path

    _row(env)
    _mark_article(env)
    moves, _ = plan(env["db"], env["vault"])
    src = Path(env["vault"]) / "Books" / TITLE
    os.chmod(src, 0o500)
    try:
        assert unwritable(env["vault"], moves) == [str(src)]
    finally:
        os.chmod(src, 0o700)


def test_nothing_is_unwritable_when_permissions_allow(env):
    _row(env)
    _mark_article(env)
    moves, _ = plan(env["db"], env["vault"])

    assert unwritable(env["vault"], moves) == []
