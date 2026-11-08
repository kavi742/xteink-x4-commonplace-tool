"""
Tests for the post-match-method-switch cleanup.

Switching Match Method to Filename re-keys every document. Clipping sync sends
only the primary hash, so the device re-uploads its store under the new hash and
the old binary-hash rows linger. These tests pin the safety rules: only drop a
row that provably has a counterpart under a filename hash, and never touch a
document whose method cannot be proven.
"""
import hashlib
import sqlite3

import pytest

from xteink_service.cleanup_match_switch import (
    apply_changes,
    plan_alias_fixes,
    plan_clipping_dedup,
)

FILENAME = "Son of Nobody_ A Novel - Yann Martel.epub"
NEW_DOC = hashlib.md5(FILENAME.encode()).hexdigest()
OLD_DOC = "52872d9fc5" + "0" * 22  # a binary-method hash: not md5(FILENAME)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE document_aliases (
            hash TEXT PRIMARY KEY, title TEXT NOT NULL,
            filename TEXT DEFAULT '', resolved_by TEXT DEFAULT 'manual',
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE clippings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL,
            document TEXT NOT NULL, clip_id TEXT NOT NULL,
            deleted INTEGER DEFAULT 0, text TEXT,
            UNIQUE(document, clip_id)
        );
    """)
    conn.commit()
    yield conn
    conn.close()


def _alias(conn, hash_, title, filename=""):
    conn.execute(
        "INSERT INTO document_aliases (hash, title, filename) VALUES (?, ?, ?)",
        (hash_, title, filename),
    )
    conn.commit()


def _clip(conn, document, clip_id, text, rev=1):
    conn.execute(
        "INSERT INTO clippings (revision, document, clip_id, text) VALUES (?, ?, ?, ?)",
        (rev, document, clip_id, text),
    )
    conn.commit()


def test_supersed_row_matched_on_clip_id_is_dropped(db):
    _alias(db, OLD_DOC, "Son of Nobody: A Novel", FILENAME)
    _alias(db, NEW_DOC, "Son of Nobody_ A Novel - Yann Martel", FILENAME)
    _clip(db, OLD_DOC, "abc123", "Keep this line.")
    _clip(db, NEW_DOC, "abc123", "Keep this line.")

    deletions, _ = plan_clipping_dedup(db)

    assert [d["document"] for d in deletions] == [OLD_DOC]
    assert deletions[0]["rule"] == "clip_id"


def test_supersed_row_matched_on_text_is_dropped(db):
    _alias(db, OLD_DOC, "Son of Nobody: A Novel", FILENAME)
    _alias(db, NEW_DOC, "Son of Nobody_ A Novel - Yann Martel", FILENAME)
    _clip(db, OLD_DOC, "oldid", "  Same   words here. ")
    _clip(db, NEW_DOC, "newid", "Same words here.")

    deletions, _ = plan_clipping_dedup(db)

    assert len(deletions) == 1
    assert deletions[0]["rule"] == "text"


def test_orphan_without_counterpart_is_kept(db):
    _alias(db, OLD_DOC, "Son of Nobody: A Novel", FILENAME)
    _alias(db, NEW_DOC, "Son of Nobody_ A Novel - Yann Martel", FILENAME)
    _clip(db, OLD_DOC, "lonely", "Only on the old hash.")
    _clip(db, NEW_DOC, "other", "Something else entirely.")

    deletions, notes = plan_clipping_dedup(db)

    assert deletions == []
    assert any("KEPT" in n for n in notes)


def test_no_filename_hash_copy_means_no_deletion(db):
    """Before the device has re-synced, the old rows are the only copy."""
    _alias(db, OLD_DOC, "Son of Nobody: A Novel", FILENAME)
    _clip(db, OLD_DOC, "abc123", "Keep this line.")

    deletions, notes = plan_clipping_dedup(db)

    assert deletions == []
    assert any("left alone" in n for n in notes)


def test_alias_without_filename_is_never_classified(db):
    _alias(db, OLD_DOC, "Son of Nobody: A Novel", "")
    _clip(db, OLD_DOC, "abc123", "Keep this line.")

    deletions, notes = plan_clipping_dedup(db)

    assert deletions == []
    assert any("no filename" in n for n in notes)


def test_error_alias_is_repaired_from_filename(db):
    _alias(
        db,
        "7f05296d211ba51e75b43a08e5826290",
        "ERROR: Error reading EPUB format",
        "Life of Pi - Yann Martel (349).epub",
    )

    fixes = plan_alias_fixes(db)

    assert len(fixes) == 1
    assert fixes[0]["new_title"] == "Life of Pi - Yann Martel (349)"
    # Canonicalises to the same slug as the healthy "Life of Pi" rows, which is
    # what re-links it for cross-device progress.
    assert fixes[0]["new_slug"] == "life-of-pi"


def test_healthy_alias_is_untouched(db):
    _alias(db, NEW_DOC, "Life of Pi", "Life of Pi - Yann Martel.epub")
    assert plan_alias_fixes(db) == []


def test_apply_is_transactional_and_preserves_extra_columns(db):
    db.execute("ALTER TABLE document_aliases ADD COLUMN kind TEXT DEFAULT 'book'")
    db.execute(
        "INSERT INTO document_aliases (hash, title, filename, kind) VALUES (?, ?, ?, 'article')",
        ("7f05296d211ba51e75b43a08e5826290", "ERROR: broken", "A Long Read.epub"),
    )
    _alias(db, OLD_DOC, "Son of Nobody: A Novel", FILENAME)
    _alias(db, NEW_DOC, "Son of Nobody_ A Novel - Yann Martel", FILENAME)
    _clip(db, OLD_DOC, "abc123", "Keep this line.")
    _clip(db, NEW_DOC, "abc123", "Keep this line.")

    deletions, _ = plan_clipping_dedup(db)
    apply_changes(db, deletions, plan_alias_fixes(db))

    assert db.execute("SELECT COUNT(*) FROM clippings").fetchone()[0] == 1
    assert db.execute("SELECT document FROM clippings").fetchone()[0] == NEW_DOC
    row = db.execute(
        "SELECT title, kind FROM document_aliases WHERE hash = ?",
        ("7f05296d211ba51e75b43a08e5826290",),
    ).fetchone()
    assert row["title"] == "A Long Read"
    assert row["kind"] == "article"
