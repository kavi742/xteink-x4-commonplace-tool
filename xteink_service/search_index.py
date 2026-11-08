"""Full-text search over every passage in the archive.

`search_docs` holds one row per indexable passage, drawn from five sources:
screenshot OCR, a screenshot's user notes, highlights, clippings, and writing
entries. `search_index` is an external-content FTS5 table over it, so
`snippet()` can read the stored text back out instead of duplicating it.

bm25() returns a NEGATIVE score where more negative is a better match, so
ranking sorts ASC. The 10x weight on the first column makes a book-title match
outrank a body match.
"""
import html
import json
import logging
import os
import re
import sqlite3
import time

from xteink_service.booktitle import canonical_book_slug

logger = logging.getLogger(__name__)

KINDS = ("screenshot", "note", "highlight", "clipping", "writing")

_ANCHOR_RE = re.compile(r"\(\(ref:(\d+)\)\)")
_NON_TOKEN_RE = re.compile(r"[^\w']", re.UNICODE)
_ALNUM_RE = re.compile(r"[^\W_]", re.UNICODE)

# snippet() marks matches with these control characters rather than real tags, so
# indexed text containing a literal "<mark>" cannot forge one past the escape.
_MARK_START = "\x02"
_MARK_END = "\x03"


def _state_db() -> str:
    return os.getenv("STATE_DB", "/data/state/state.db")


# ------------------------------------------------------------------ #
# Schema                                                               #
# ------------------------------------------------------------------ #

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS search_docs (
        doc_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        kind          TEXT    NOT NULL,
        ref_id        INTEGER NOT NULL,
        book_slug     TEXT    NOT NULL DEFAULT '',
        book_title    TEXT    NOT NULL DEFAULT '',
        body          TEXT    NOT NULL DEFAULT '',
        sort_date     TEXT    NOT NULL DEFAULT '',
        screenshot_id INTEGER,
        updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(kind, ref_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_search_docs_slug ON search_docs (book_slug)",
    "CREATE INDEX IF NOT EXISTS idx_search_docs_kind ON search_docs (kind)",
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
        book_title,
        body,
        content='search_docs',
        content_rowid='doc_id',
        tokenize="unicode61 remove_diacritics 2"
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS search_docs_ai AFTER INSERT ON search_docs BEGIN
        INSERT INTO search_index(rowid, book_title, body)
        VALUES (new.doc_id, new.book_title, new.body);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS search_docs_ad AFTER DELETE ON search_docs BEGIN
        INSERT INTO search_index(search_index, rowid, book_title, body)
        VALUES ('delete', old.doc_id, old.book_title, old.body);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS search_docs_au AFTER UPDATE ON search_docs BEGIN
        INSERT INTO search_index(search_index, rowid, book_title, body)
        VALUES ('delete', old.doc_id, old.book_title, old.body);
        INSERT INTO search_index(rowid, book_title, body)
        VALUES (new.doc_id, new.book_title, new.body);
    END
    """,
)


def init_db(conn: sqlite3.Connection) -> None:
    for statement in _DDL:
        conn.execute(statement)


def _register_slug_fn(conn: sqlite3.Connection) -> None:
    conn.create_function("canon_slug", 1, canonical_book_slug, deterministic=True)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _register_slug_fn(conn)
    conn.commit()
    return conn


# ------------------------------------------------------------------ #
# Query and snippet handling                                           #
# ------------------------------------------------------------------ #

def to_match_query(q: str) -> str | None:
    """Turn raw user input into an FTS5 MATCH expression, or None if nothing survives.

    Everything outside a word character or an apostrophe is dropped, so operators
    (`AND`, `NEAR(`, `*`, `^`) and stray quotes can never reach the parser.
    """
    tokens = []
    for raw in (q or "").split():
        token = _NON_TOKEN_RE.sub("", raw)
        # A token the tokenizer would reduce to nothing (all apostrophes or
        # underscores) becomes an empty phrase, which FTS5 refuses to parse.
        if token and _ALNUM_RE.search(token):
            tokens.append(f'"{token}"')
    if not tokens:
        return None
    tokens[-1] += "*"
    return " ".join(tokens)


def _escape_snippet(snippet: str) -> str:
    """HTML-escape the whole snippet, then restore only SQLite's own marks."""
    escaped = html.escape(snippet or "", quote=False)
    return escaped.replace(_MARK_START, "<mark>").replace(_MARK_END, "</mark>")


def expand_anchors(body: str, snapshots: dict[int, str]) -> str:
    """Replace every ((ref:N)) with snapshots[N], dropping ids that do not resolve."""
    return _ANCHOR_RE.sub(lambda m: snapshots.get(int(m.group(1)), ""), body)


# ------------------------------------------------------------------ #
# Single-document maintenance                                          #
# ------------------------------------------------------------------ #

_UPSERT = """
    INSERT INTO search_docs
           (kind, ref_id, book_slug, book_title, body, sort_date, screenshot_id)
    VALUES (?,    ?,      ?,         ?,          ?,    ?,         ?)
    ON CONFLICT(kind, ref_id) DO UPDATE SET
        book_slug     = excluded.book_slug,
        book_title    = excluded.book_title,
        body          = excluded.body,
        sort_date     = excluded.sort_date,
        screenshot_id = excluded.screenshot_id,
        updated_at    = CURRENT_TIMESTAMP
"""


def _upsert(
    conn: sqlite3.Connection,
    kind: str,
    ref_id: int,
    book_title: str,
    body: str,
    sort_date: str,
    screenshot_id: int | None,
) -> None:
    conn.execute(
        _UPSERT,
        (kind, ref_id, canonical_book_slug(book_title), book_title or "",
         body or "", sort_date or "", screenshot_id),
    )


def unindex(conn: sqlite3.Connection, kind: str, ref_id: int) -> None:
    conn.execute("DELETE FROM search_docs WHERE kind = ? AND ref_id = ?", (kind, ref_id))


def index_screenshot(conn: sqlite3.Connection, screenshot_id: int) -> None:
    row = conn.execute(
        "SELECT COALESCE(book_title, ''), "
        "COALESCE(NULLIF(ocr_corrected, ''), ocr_text, ''), COALESCE(sync_date, '') "
        "FROM synced_screenshots WHERE id = ?",
        (screenshot_id,),
    ).fetchone()
    if row is None or (not row[0] and not row[1]):
        unindex(conn, "screenshot", screenshot_id)
        return
    _upsert(conn, "screenshot", screenshot_id, row[0], row[1], row[2], screenshot_id)


def index_note(conn: sqlite3.Connection, screenshot_id: int) -> None:
    row = conn.execute(
        "SELECT COALESCE(book_title, ''), COALESCE(user_notes, ''), COALESCE(sync_date, '') "
        "FROM synced_screenshots WHERE id = ?",
        (screenshot_id,),
    ).fetchone()
    if row is None or not row[1]:
        unindex(conn, "note", screenshot_id)
        return
    _upsert(conn, "note", screenshot_id, row[0], row[1], row[2], screenshot_id)


def index_highlight(conn: sqlite3.Connection, highlight_id: int) -> None:
    row = conn.execute(
        "SELECT h.selected_text, h.screenshot_id, COALESCE(s.book_title, ''), "
        "COALESCE(s.sync_date, '') "
        "FROM highlights h JOIN synced_screenshots s ON s.id = h.screenshot_id "
        "WHERE h.id = ?",
        (highlight_id,),
    ).fetchone()
    if row is None or not row[0]:
        unindex(conn, "highlight", highlight_id)
        return
    _upsert(conn, "highlight", highlight_id, row[2], row[0], row[3], row[1])


def index_clipping(conn: sqlite3.Connection, clipping_id: int) -> None:
    row = conn.execute(
        "SELECT c.deleted, c.text, COALESCE(a.title, ''), "
        "CASE WHEN c.created_at > 0 THEN date(c.created_at, 'unixepoch') "
        "     ELSE substr(COALESCE(c.synced_at, ''), 1, 10) END "
        "FROM clippings c LEFT JOIN document_aliases a ON a.hash = c.document "
        "WHERE c.id = ?",
        (clipping_id,),
    ).fetchone()
    if row is None or row[0] or not row[1]:
        unindex(conn, "clipping", clipping_id)
        return
    _upsert(conn, "clipping", clipping_id, row[2], row[1], row[3], None)


def _titles_by_slug(conn: sqlite3.Connection) -> dict[str, str]:
    """Slug to a displayable book title, from whichever source names the book."""
    out: dict[str, str] = {}
    for sql in (
        "SELECT DISTINCT book_title FROM synced_screenshots WHERE COALESCE(book_title, '') != ''",
        "SELECT title FROM document_aliases WHERE COALESCE(title, '') != ''",
    ):
        try:
            for (title,) in conn.execute(sql):
                out.setdefault(canonical_book_slug(title), title)
        except sqlite3.OperationalError:
            continue
    return out


def _upsert_writing(
    conn: sqlite3.Connection,
    entry: sqlite3.Row,
    snapshots: dict[int, str],
    titles: dict[str, str],
) -> None:
    entry_id, book_slug, entry_date, title, body = entry
    expanded = expand_anchors(body or "", snapshots)
    conn.execute(
        _UPSERT,
        ("writing", entry_id, book_slug or "", titles.get(book_slug or "", ""),
         f"{title or ''}\n\n{expanded}", entry_date or "", None),
    )


def index_writing(conn: sqlite3.Connection, entry_id: int) -> None:
    entry = conn.execute(
        "SELECT id, book_slug, entry_date, title, body FROM writing_entries "
        "WHERE id = ? AND deleted_at IS NULL",
        (entry_id,),
    ).fetchone()
    if entry is None:
        unindex(conn, "writing", entry_id)
        return
    snapshots = {
        ref_id: snapshot or ""
        for ref_id, snapshot in conn.execute(
            "SELECT id, snapshot FROM writing_refs WHERE entry_id = ?", (entry_id,)
        )
    }
    _upsert_writing(conn, entry, snapshots, _titles_by_slug(conn))


def index_writing_for_ref(conn: sqlite3.Connection, ref_id: int) -> None:
    row = conn.execute("SELECT entry_id FROM writing_refs WHERE id = ?", (ref_id,)).fetchone()
    if row is not None:
        index_writing(conn, row[0])


# ------------------------------------------------------------------ #
# Rebuild                                                              #
# ------------------------------------------------------------------ #

_REBUILD = (
    ("screenshot", """
        INSERT INTO search_docs
               (kind, ref_id, book_slug, book_title, body, sort_date, screenshot_id)
        SELECT 'screenshot', s.id, canon_slug(s.book_title), COALESCE(s.book_title, ''),
               COALESCE(NULLIF(s.ocr_corrected, ''), s.ocr_text, ''),
               COALESCE(s.sync_date, ''), s.id
        FROM synced_screenshots s
        WHERE COALESCE(NULLIF(s.ocr_corrected, ''), s.ocr_text, '') != ''
           OR COALESCE(s.book_title, '') != ''
    """),
    ("note", """
        INSERT INTO search_docs
               (kind, ref_id, book_slug, book_title, body, sort_date, screenshot_id)
        SELECT 'note', s.id, canon_slug(s.book_title), COALESCE(s.book_title, ''),
               s.user_notes, COALESCE(s.sync_date, ''), s.id
        FROM synced_screenshots s
        WHERE s.user_notes IS NOT NULL AND s.user_notes != ''
    """),
    ("highlight", """
        INSERT INTO search_docs
               (kind, ref_id, book_slug, book_title, body, sort_date, screenshot_id)
        SELECT 'highlight', h.id, canon_slug(s.book_title), COALESCE(s.book_title, ''),
               h.selected_text, COALESCE(s.sync_date, ''), h.screenshot_id
        FROM highlights h
        JOIN synced_screenshots s ON s.id = h.screenshot_id
        WHERE h.selected_text != ''
    """),
    ("clipping", """
        INSERT INTO search_docs
               (kind, ref_id, book_slug, book_title, body, sort_date, screenshot_id)
        SELECT 'clipping', c.id, canon_slug(a.title), COALESCE(a.title, ''), c.text,
               CASE WHEN c.created_at > 0
                    THEN date(c.created_at, 'unixepoch')
                    ELSE substr(COALESCE(c.synced_at, ''), 1, 10) END,
               NULL
        FROM clippings c
        LEFT JOIN document_aliases a ON a.hash = c.document
        WHERE c.deleted = 0 AND c.text != ''
    """),
)


def _rebuild_writing(conn: sqlite3.Connection) -> int:
    entries = conn.execute(
        "SELECT e.id, e.book_slug, e.entry_date, e.title, e.body "
        "FROM writing_entries e WHERE e.deleted_at IS NULL"
    ).fetchall()
    refs: dict[int, dict[int, str]] = {}
    for entry_id, ref_id, snapshot in conn.execute(
        "SELECT r.entry_id, r.id, r.snapshot FROM writing_refs r "
        "JOIN writing_entries e ON e.id = r.entry_id WHERE e.deleted_at IS NULL"
    ):
        refs.setdefault(entry_id, {})[ref_id] = snapshot or ""
    titles = _titles_by_slug(conn)
    for entry in entries:
        _upsert_writing(conn, entry, refs.get(entry[0], {}), titles)
    return len(entries)


def rebuild(db_path: str | None = None) -> dict:
    """Re-derive every search_docs row from its source table. Idempotent."""
    started = time.monotonic()
    conn = _connect(db_path or _state_db())
    indexed = dict.fromkeys(KINDS, 0)
    skipped: list[str] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM search_docs")
        for kind, sql in _REBUILD:
            try:
                indexed[kind] = conn.execute(sql).rowcount
            except sqlite3.OperationalError as exc:
                skipped.append(kind)
                logger.info("Search reindex skipped %s: %s", kind, exc)
        try:
            indexed["writing"] = _rebuild_writing(conn)
        except sqlite3.OperationalError as exc:
            skipped.append("writing")
            logger.info("Search reindex skipped writing: %s", exc)
        conn.execute("INSERT INTO search_index(search_index) VALUES('rebuild')")
        conn.commit()
    finally:
        conn.close()
    duration_ms = round((time.monotonic() - started) * 1000)
    logger.info("Search reindex: %s skipped=%s in %dms", indexed, skipped, duration_ms)
    return {"indexed": indexed, "skipped": skipped, "duration_ms": duration_ms}


def ensure_built(db_path: str | None = None) -> dict | None:
    """Build the index once, on a database that has none. None if already built."""
    db_path = db_path or _state_db()
    conn = _connect(db_path)
    try:
        built = conn.execute("SELECT 1 FROM search_docs LIMIT 1").fetchone() is not None
    finally:
        conn.close()
    return None if built else rebuild(db_path)


# ------------------------------------------------------------------ #
# Search                                                               #
# ------------------------------------------------------------------ #

_FILTERS = """
      AND (:kinds_csv = '' OR d.kind IN (SELECT value FROM json_each(:kinds_json)))
      AND (:book_slug = '' OR d.book_slug = :book_slug)
"""

_SEARCH_SQL = f"""
    SELECT d.kind, d.ref_id, d.book_slug, d.book_title, d.sort_date, d.screenshot_id,
           snippet(search_index, 1, char(2), char(3), '...', 24) AS snippet,
           bm25(search_index, 10.0, 1.0)                         AS score
    FROM search_index
    JOIN search_docs d ON d.doc_id = search_index.rowid
    WHERE search_index MATCH :match
    {_FILTERS}
    ORDER BY score
    LIMIT :limit OFFSET :offset
"""

_BROWSE_SQL = f"""
    SELECT d.kind, d.ref_id, d.book_slug, d.book_title, d.sort_date, d.screenshot_id,
           substr(d.body, 1, 160) AS snippet, 0.0 AS score
    FROM search_docs d
    WHERE 1 = 1
    {_FILTERS}
    ORDER BY d.book_title, d.sort_date, d.ref_id
    LIMIT :limit OFFSET :offset
"""

_FACET_SQL = """
    SELECT d.kind, COUNT(*) AS n
    FROM search_index
    JOIN search_docs d ON d.doc_id = search_index.rowid
    WHERE search_index MATCH :match
      AND (:book_slug = '' OR d.book_slug = :book_slug)
    GROUP BY d.kind
"""

_BROWSE_FACET_SQL = """
    SELECT d.kind, COUNT(*) AS n
    FROM search_docs d
    WHERE (:book_slug = '' OR d.book_slug = :book_slug)
    GROUP BY d.kind
"""


def search(
    db_path: str | None = None,
    q: str = "",
    kinds: list[str] | None = None,
    book: str = "",
    limit: int = 50,
    offset: int = 0,
    match_all: bool = False,
) -> dict:
    """Ranked passage search. `match_all` lists everything when `q` has no tokens."""
    wanted = [k for k in (kinds or []) if k in KINDS]
    out = {
        "q": q, "kinds": wanted, "book": book, "limit": limit, "offset": offset,
        "total": 0, "facets": dict.fromkeys(KINDS, 0), "results": [],
    }
    match = to_match_query(q)
    if match is None and not match_all:
        return out

    params = {
        "match": match,
        "kinds_csv": ",".join(wanted),
        "kinds_json": json.dumps(wanted),
        "book_slug": book,
        "limit": limit,
        "offset": offset,
    }
    conn = _connect(db_path or _state_db())
    try:
        # Facets ignore the kinds filter so the chips keep showing the other counts.
        facet_sql = _FACET_SQL if match else _BROWSE_FACET_SQL
        for row in conn.execute(facet_sql, params):
            if row["kind"] in out["facets"]:
                out["facets"][row["kind"]] = row["n"]
        rows = conn.execute(_SEARCH_SQL if match else _BROWSE_SQL, params).fetchall()
    finally:
        conn.close()

    if match:
        out["total"] = sum(out["facets"].values())
    else:
        out["total"] = sum(out["facets"][k] for k in (wanted or KINDS))
    out["results"] = [
        {
            "kind": r["kind"],
            "ref_id": r["ref_id"],
            "screenshot_id": r["screenshot_id"],
            "book_title": r["book_title"],
            "book_slug": r["book_slug"],
            "date": r["sort_date"],
            "snippet": _escape_snippet(r["snippet"]),
            "score": r["score"],
        }
        for r in rows
    ]
    return out
