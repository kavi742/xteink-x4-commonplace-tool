import hashlib
import sqlite3


class SyncState:
    """SQLite-backed dedup table for screenshot archiving."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS synced_screenshots (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_path  TEXT    NOT NULL,
                    content_hash TEXT    NOT NULL,
                    synced_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    book_title   TEXT    DEFAULT '',
                    sync_date    TEXT    DEFAULT '',
                    ocr_text     TEXT,
                    UNIQUE(device_path, content_hash)
                )
            """)
            # document_aliases populated in Phase 6b
            conn.execute("""
                CREATE TABLE IF NOT EXISTS document_aliases (
                    hash         TEXT PRIMARY KEY,
                    title        TEXT NOT NULL,
                    filename     TEXT DEFAULT '',
                    resolved_by  TEXT DEFAULT 'manual',
                    computed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    # ------------------------------------------------------------------ #
    # Screenshot dedup                                                     #
    # ------------------------------------------------------------------ #

    def is_path_synced(self, device_path: str) -> bool:
        """True if this path has been archived before (any hash version)."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM synced_screenshots WHERE device_path = ? LIMIT 1",
                (device_path,),
            ).fetchone()
        return row is not None

    def is_synced(self, device_path: str, content_hash: str) -> bool:
        """True if this exact (path, hash) pair has been archived."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM synced_screenshots "
                "WHERE device_path = ? AND content_hash = ? LIMIT 1",
                (device_path, content_hash),
            ).fetchone()
        return row is not None

    def mark_synced(
        self,
        device_path: str,
        content_hash: str,
        book_title: str = "",
        sync_date: str = "",
        ocr_text: str | None = None,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO synced_screenshots "
                "(device_path, content_hash, book_title, sync_date, ocr_text) "
                "VALUES (?, ?, ?, ?, ?)",
                (device_path, content_hash, book_title, sync_date, ocr_text),
            )

    # ------------------------------------------------------------------ #
    # Document alias lookup                                                #
    # ------------------------------------------------------------------ #

    def get_title(self, doc_hash: str) -> str | None:
        """Return the resolved title for a progress hash, or None."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT title FROM document_aliases WHERE hash = ?",
                (doc_hash,),
            ).fetchone()
        return row[0] if row else None
