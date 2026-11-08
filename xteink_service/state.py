import hashlib
import json
import logging
import sqlite3

from xteink_service import search_index
from xteink_service.content_kind import kind_for_device_path

logger = logging.getLogger(__name__)


def _index(fn, conn: sqlite3.Connection, *args) -> None:
    """Index maintenance must never break a sync; a stale index is recoverable."""
    try:
        fn(conn, *args)
    except sqlite3.OperationalError as exc:
        logger.warning("Search index update skipped: %s", exc)


# ------------------------------------------------------------------ #
# Citations                                                            #
# ------------------------------------------------------------------ #
# Deleting a passage must not gut the writing that quotes it. The entry body is
# never touched; only the citing ref is flagged, so the reader can be told its
# source is gone. `writing_refs` belongs to a later plan item and does not exist
# yet, which is why every statement here swallows OperationalError.

def mark_citations(conn: sqlite3.Connection, kind: str, ref_id: int) -> list[int]:
    """Flag every live ref citing this passage as deleted. Returns the ids flagged."""
    try:
        ids = [
            row[0] for row in conn.execute(
                "SELECT id FROM writing_refs "
                "WHERE kind = ? AND ref_id = ? AND source_state = 'live'",
                (kind, ref_id),
            )
        ]
        if ids:
            conn.execute(
                "UPDATE writing_refs SET source_state = 'deleted' "
                "WHERE kind = ? AND ref_id = ? AND source_state = 'live'",
                (kind, ref_id),
            )
        return ids
    except sqlite3.OperationalError:
        return []


def deleted_citation_ids(conn: sqlite3.Connection, kind: str, ref_id: int) -> list[int]:
    """Refs citing this passage that a delete flagged, for a restore with no payload."""
    try:
        return [
            row[0] for row in conn.execute(
                "SELECT id FROM writing_refs "
                "WHERE kind = ? AND ref_id = ? AND source_state = 'deleted'",
                (kind, ref_id),
            )
        ]
    except sqlite3.OperationalError:
        return []


def restore_citations(conn: sqlite3.Connection, ref_ids: list[int]) -> int:
    """Return the named refs to live. A ref marked 'drifted' elsewhere is left alone."""
    ids = [int(i) for i in (ref_ids or [])]
    if not ids:
        return 0
    try:
        cur = conn.execute(
            "UPDATE writing_refs SET source_state = 'live' "
            "WHERE id IN (SELECT value FROM json_each(?)) AND source_state = 'deleted'",
            (json.dumps(ids),),
        )
        return cur.rowcount
    except sqlite3.OperationalError:
        return 0


def citation_clause(ref_count: int) -> str:
    """The toast sentence telling the user the writing kept this passage's words."""
    if ref_count <= 0:
        return ""
    noun = "citation" if ref_count == 1 else "citations"
    verb = "keeps" if ref_count == 1 else "keep"
    return f" {ref_count} {noun} {verb} its text."


def _backfill_alias_kinds(conn: sqlite3.Connection) -> None:
    """Classify pre-existing aliases that recorded a full device path.

    Most rows only stored a bare filename, which carries no directory and so no
    evidence either way. Those stay 'book' rather than being guessed at.
    """
    rows = conn.execute(
        "SELECT hash, filename FROM document_aliases WHERE filename LIKE '%/%'"
    ).fetchall()
    articles = [
        (h,) for h, filename in rows
        if kind_for_device_path(filename) == "article"
    ]
    if articles:
        conn.executemany(
            "UPDATE document_aliases SET kind = 'article' WHERE hash = ?", articles
        )
        logger.info("Backfilled %d alias(es) as articles", len(articles))


class SyncState:
    """SQLite-backed dedup table for screenshot archiving."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            logger.debug("Initialising DB at %s", self.db_path)
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
                    vault_root     TEXT    DEFAULT 'Books',
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
                    deleted_at     TIMESTAMP,
                    FOREIGN KEY(screenshot_id) REFERENCES synced_screenshots(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tbr_books (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    title      TEXT    NOT NULL,
                    author     TEXT    DEFAULT '',
                    source_url TEXT    DEFAULT '',
                    notes      TEXT    DEFAULT '',
                    status     TEXT    DEFAULT 'queued',
                    sort_order INTEGER DEFAULT 0,
                    added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Migrate existing DBs that predate the new columns
            for col, typedef in [
                ("vault_png_path", "TEXT DEFAULT ''"),
                ("ocr_corrected",  "TEXT"),
                ("user_notes",     "TEXT"),
                ("vault_root",     "TEXT DEFAULT 'Books'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE synced_screenshots ADD COLUMN {col} {typedef}")
                except sqlite3.OperationalError:
                    pass  # column already exists
            for col, typedef in [
                ("deleted_at", "TIMESTAMP"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE highlights ADD COLUMN {col} {typedef}")
                except sqlite3.OperationalError:
                    pass  # column already exists
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_highlights_shot_live "
                "ON highlights (screenshot_id) WHERE deleted_at IS NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_highlights_deleted "
                "ON highlights (deleted_at) WHERE deleted_at IS NOT NULL"
            )
            # document_aliases populated in Phase 6b
            conn.execute("""
                CREATE TABLE IF NOT EXISTS document_aliases (
                    hash         TEXT PRIMARY KEY,
                    title        TEXT NOT NULL,
                    filename     TEXT DEFAULT '',
                    resolved_by  TEXT DEFAULT 'manual',
                    computed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    kind         TEXT DEFAULT 'book'
                )
            """)
            # Full device paths seen during the last File Transfer walk. kosync
            # only ever sends a basename, so without this an alias created by a
            # progress push cannot be told apart from a book until the NEXT walk.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_files (
                    hash    TEXT PRIMARY KEY,
                    path    TEXT NOT NULL,
                    seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            try:
                conn.execute(
                    "ALTER TABLE document_aliases ADD COLUMN kind TEXT DEFAULT 'book'"
                )
            except Exception:
                pass  # column already exists
            else:
                # Only reachable the one time the column is added.
                _backfill_alias_kinds(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aliases_kind ON document_aliases(kind)"
            )
            search_index.init_db(conn)

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
        vault_root: str = "Books",
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO synced_screenshots "
                "(device_path, content_hash, book_title, sync_date, ocr_text, "
                "vault_png_path, vault_root) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (device_path, content_hash, book_title, sync_date, ocr_text,
                 vault_png_path, vault_root),
            )
            # INSERT OR IGNORE yields no lastrowid on conflict, so re-read the id.
            row = conn.execute(
                "SELECT id FROM synced_screenshots "
                "WHERE device_path = ? AND content_hash = ?",
                (device_path, content_hash),
            ).fetchone()
            if row:
                _index(search_index.index_screenshot, conn, row[0])
        logger.debug("Marked synced: %s", device_path.split('/')[-1])

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
            _index(search_index.index_screenshot, conn, screenshot_id)
            _index(search_index.index_note, conn, screenshot_id)

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

    def get_kind(self, doc_hash: str) -> str:
        """Content kind for a progress hash, defaulting to 'book'."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT kind FROM document_aliases WHERE hash = ?",
                (doc_hash,),
            ).fetchone()
        return (row[0] if row else None) or "book"

    def set_title(
        self, doc_hash: str, title: str, filename: str = "",
        resolved_by: str = "manual", kind: str | None = None,
    ) -> None:
        """Map a progress hash to a title, leaving any existing mapping alone.

        Used for titles a client volunteers via KOReader's `metadata` payload:
        a hand-corrected alias must win over whatever the device reports.

        Pass kind=None when the caller cannot know it, which is the case for
        kosync metadata: the firmware sends a bare basename. The column default
        applies and a later device walk reconciles it.
        """
        with sqlite3.connect(self.db_path) as conn:
            if kind is None:
                conn.execute(
                    "INSERT OR IGNORE INTO document_aliases "
                    "(hash, title, filename, resolved_by) VALUES (?,?,?,?)",
                    (doc_hash, title, filename, resolved_by),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO document_aliases "
                    "(hash, title, filename, resolved_by, kind) VALUES (?,?,?,?,?)",
                    (doc_hash, title, filename, resolved_by, kind),
                )
            # A clipping can arrive before its alias, and index_clipping
            # denormalises the title, so it would stay indexed namelessly.
            _index(search_index.index_clippings_for_document, conn, doc_hash)

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
            _index(search_index.index_highlight, conn, cur.lastrowid)
            row = conn.execute(
                "SELECT * FROM highlights WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return dict(row)

    def list_highlights(self, screenshot_id: int) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM highlights WHERE screenshot_id = ? AND deleted_at IS NULL "
                "ORDER BY id",
                (screenshot_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_highlight(self, highlight_id: int) -> dict | None:
        """One highlight joined to the screenshot that names its book."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT h.*, s.book_title, s.vault_root, s.sync_date "
                "FROM highlights h JOIN synced_screenshots s ON s.id = h.screenshot_id "
                "WHERE h.id = ?",
                (highlight_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_highlight(self, highlight_id: int) -> bool:
        """Soft delete. False when the row is missing or already in the trash."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE highlights SET deleted_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND deleted_at IS NULL",
                (highlight_id,),
            )
            _index(search_index.unindex, conn, "highlight", highlight_id)
        return cur.rowcount > 0

    def purge_highlight(self, highlight_id: int) -> bool:
        """Hard delete, but only from the trash: a live highlight is never purgeable."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM highlights WHERE id = ? AND deleted_at IS NOT NULL",
                (highlight_id,),
            )
        return cur.rowcount > 0

    def purge_trashed_highlights(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM highlights WHERE deleted_at IS NOT NULL")
        return cur.rowcount

    def list_trashed_highlights(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT h.id, h.screenshot_id, h.selected_text, h.deleted_at, "
                "       COALESCE(s.book_title, '') AS book_title, "
                "       COALESCE(s.sync_date, '') AS sync_date "
                "FROM highlights h "
                "LEFT JOIN synced_screenshots s ON s.id = h.screenshot_id "
                "WHERE h.deleted_at IS NOT NULL "
                "ORDER BY h.deleted_at DESC, h.id DESC"
            ).fetchall()
        return [dict(r) for r in rows]
