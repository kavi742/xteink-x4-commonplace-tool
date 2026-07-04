import pytest
from xteink_service.state import SyncState


@pytest.fixture
def state(tmp_path):
    return SyncState(str(tmp_path / "state.db"))


def test_not_synced_initially(state):
    assert not state.is_path_synced("/screenshots/Book/file.bmp")
    assert not state.is_synced("/screenshots/Book/file.bmp", "abc123")


def test_mark_and_check_path(state):
    state.mark_synced("/screenshots/Book/a.bmp", "hash1", "Book", "2026-07-04")
    assert state.is_path_synced("/screenshots/Book/a.bmp")
    assert not state.is_path_synced("/screenshots/Book/b.bmp")


def test_mark_and_check_exact(state):
    state.mark_synced("/screenshots/Book/a.bmp", "hash1")
    assert state.is_synced("/screenshots/Book/a.bmp", "hash1")
    assert not state.is_synced("/screenshots/Book/a.bmp", "different_hash")


def test_idempotent_mark(state):
    """Marking the same (path, hash) twice must not raise or duplicate."""
    state.mark_synced("/screenshots/Book/a.bmp", "hash1")
    state.mark_synced("/screenshots/Book/a.bmp", "hash1")  # second call is a no-op
    assert state.is_path_synced("/screenshots/Book/a.bmp")


def test_different_files_independent(state):
    state.mark_synced("/screenshots/A/a.bmp", "h1", "A", "2026-07-04")
    state.mark_synced("/screenshots/B/b.bmp", "h2", "B", "2026-07-04")
    assert state.is_path_synced("/screenshots/A/a.bmp")
    assert state.is_path_synced("/screenshots/B/b.bmp")
    assert not state.is_path_synced("/screenshots/C/c.bmp")


def test_get_title_unknown_hash(state):
    assert state.get_title("unknownhash") is None


def test_get_title_after_alias(state):
    with __import__("sqlite3").connect(state.db_path) as conn:
        conn.execute(
            "INSERT INTO document_aliases (hash, title) VALUES (?, ?)",
            ("abc123", "Pastoral"),
        )
    assert state.get_title("abc123") == "Pastoral"
