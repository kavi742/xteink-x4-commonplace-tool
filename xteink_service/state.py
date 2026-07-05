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
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_path    TEXT    NOT NULL,
                    content_hash   TEXT    NOT NULL,
                    synced_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    book_title     TEXT    DEFAULT '',
                    sync_date      TEXT    DEFAULT '',
                    ocr_text       TEXT,
                    vault_png_path TEXT    DEFAULT '',
                    ocr_corrected  TEXT,
                    user_notes     TEXT,
                    UNIQUE(device_path, content_hash)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS highlights (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    screenshot_id  INTEGER NOT NULL,
                    selected_text  TEXT    NOT NULL,
                    bbox_json      TEXT    DEFAULT '[]',
                    img_w          INTEGER DEFAULT 0,
                    img_h          INTEGER DEFAULT 0,
                    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(screenshot_id) REFERENCES synced_screenshots(id)
                )
            """)
            # Migrate existing DBs that predate the new columns
            for col, typedef in [
                ("vault_png_path", "TEXT DEFAULT ''"),
                ("ocr_corrected",  "TEXT"),
                ("user_notes",     "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE synced_screenshots ADD COLUMN {col} {typedef}")
                except Exception:
                    pass  # column already exists
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
        vault_png_path: str = "",
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO synced_screenshots "
                "(device_path, content_hash, book_title, sync_date, ocr_text, vault_png_path) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (device_path, content_hash, book_title, sync_date, ocr_text, vault_png_path),
            )

    def get_screenshot(self, screenshot_id: int) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM synced_screenshots WHERE id = ?", (screenshot_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_screenshot(
        self,
        screenshot_id: int,
        ocr_corrected: str | None = None,
        user_notes: str | None = None,
    ) -> None:
        fields, values = [], []
        if ocr_corrected is not None:
            fields.append("ocr_corrected = ?")
            values.append(ocr_corrected)
        if user_notes is not None:
            fields.append("user_notes = ?")
            values.append(user_notes)
        if not fields:
            return
        values.append(screenshot_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE synced_screenshots SET {', '.join(fields)} WHERE id = ?",
                values,
            )

    def list_books(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT book_title,
                       COUNT(*) as screenshot_count,
                       MAX(synced_at) as last_synced
                FROM synced_screenshots
                GROUP BY book_title
                ORDER BY book_title
            """).fetchall()
        return [dict(r) for r in rows]

    def list_screenshots(self, book_title: str) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM synced_screenshots WHERE book_title = ? ORDER BY id",
                (book_title,),
            ).fetchall()
        return [dict(r) for r in rows]

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

    # ------------------------------------------------------------------ #
    # Highlights                                                           #
    # ------------------------------------------------------------------ #

    def add_highlight(
        self,
        screenshot_id: int,
        selected_text: str,
        bbox_json: str = "[]",
        img_w: int = 0,
        img_h: int = 0,
    ) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Migrate column if needed
            try:
                conn.execute("ALTER TABLE highlights ADD COLUMN bbox_json TEXT DEFAULT '[]'")
                conn.execute("ALTER TABLE highlights ADD COLUMN img_w INTEGER DEFAULT 0")
                conn.execute("ALTER TABLE highlights ADD COLUMN img_h INTEGER DEFAULT 0")
            except Exception:
                pass
            cur = conn.execute(
                "INSERT INTO highlights (screenshot_id, selected_text, bbox_json, img_w, img_h) VALUES (?, ?, ?, ?, ?)",
                (screenshot_id, selected_text, bbox_json, img_w, img_h),
            )
            row = conn.execute(
                "SELECT * FROM highlights WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return dict(row)

    def list_highlights(self, screenshot_id: int) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM highlights WHERE screenshot_id = ? ORDER BY id",
                (screenshot_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_highlight(self, highlight_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
        return cur.rowcount > 0
