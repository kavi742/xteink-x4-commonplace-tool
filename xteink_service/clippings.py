"""
Clipping sync — the CrossPoint `/api/v1/clippings` extension.

This is a separate protocol from kosync, not an extra field on the progress
payload. CrossPoint's ClippingSync.cpp first probes the collection with
`GET ?cursor=0&limit=1&format=reader` and gives up unless the response carries
`sync_version: 2`, so the probe must answer before anything is uploaded.

Flow per book (document hash), all against /api/v1/clippings/{document}:
  1. GET probe                      — capability check
  2. PUT {"items":[{id, deleted:1}]} — tombstones the device recorded offline
  3. PUT {"items":[{...}]}           — one pending clipping per request
  4. GET ?cursor=N                   — paginate everything back down

`revision` is a server-assigned, globally monotonic counter and doubles as the
pagination cursor. Every write takes a fresh revision so other devices see the
change on their next pass. The device validates pagination strictly: at most
`limit` items, a cursor that never goes backwards, a cursor that strictly
advances whenever items are returned, and no `more: true` on an empty page.
"""
import logging
import os
import re
import sqlite3
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from xteink_service.booktitle import canonical_book_slug

logger = logging.getLogger(__name__)

SYNC_VERSION = 2

# Mirrors the firmware's own limits: CLIPPING_TEXT_MAX is 4 KB and Clipping::id
# is char[65], so an id must stay under 65 bytes to survive the device's decode.
_TEXT_MAX = 4096
_ID_MAX = 64
_ID_RE = re.compile(r"^[!-~]{1,%d}$" % _ID_MAX)

_UINT16_MAX = 0xFFFF
_UINT32_MAX = 0xFFFFFFFF


def _state_db() -> str:
    return os.getenv("STATE_DB", "/data/state/state.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_state_db())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clippings (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                revision         INTEGER NOT NULL,
                document         TEXT    NOT NULL,
                clip_id          TEXT    NOT NULL,
                deleted          INTEGER NOT NULL DEFAULT 0,
                spine            INTEGER NOT NULL DEFAULT 0,
                start_page       INTEGER NOT NULL DEFAULT 0,
                end_page         INTEGER NOT NULL DEFAULT 0,
                pages            INTEGER NOT NULL DEFAULT 1,
                start_word       INTEGER NOT NULL DEFAULT 0,
                end_word         INTEGER NOT NULL DEFAULT 0,
                words            INTEGER NOT NULL DEFAULT 0,
                para             INTEGER,
                chapter          TEXT    NOT NULL DEFAULT '',
                text             TEXT    NOT NULL DEFAULT '',
                created_at       INTEGER NOT NULL DEFAULT 0,
                layout_signature INTEGER NOT NULL DEFAULT 0,
                start_offset     INTEGER,
                end_offset       INTEGER,
                synced_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(document, clip_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_clippings_doc_rev "
            "ON clippings (document, revision)"
        )


# ------------------------------------------------------------------ #
# Payload models                                                       #
# ------------------------------------------------------------------ #

class ClippingItem(BaseModel):
    # The firmware may gain fields between releases; dropping them silently
    # would hide that, and the protocol ignores unknown keys anyway.
    model_config = ConfigDict(extra="allow")

    id: str
    deleted: int = 0
    spine: int = 0
    start_page: int = 0
    end_page: int = 0
    pages: int = 1
    start_word: int = 0
    end_word: int = 0
    words: int = 0
    para: Optional[int] = None
    chapter: str = ""
    text: str = ""
    created_at: int = 0
    layout_signature: int = 0
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None


class ClippingUpload(BaseModel):
    items: list[ClippingItem] = []


def _reject(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _validate(item: ClippingItem) -> None:
    """Reject anything the device itself would refuse to decode on the way back."""
    if not _ID_RE.match(item.id):
        raise _reject("Clipping id must be 1-64 printable ASCII characters")
    if item.deleted not in (0, 1):
        raise _reject("deleted must be 0 or 1")
    if item.deleted == 1:
        return
    text = item.text or ""
    if not text or len(text.encode()) > _TEXT_MAX:
        raise _reject(f"Clipping text must be 1-{_TEXT_MAX} bytes")
    if "\x00" in text:
        raise _reject("Clipping text must not contain NUL")
    for name in ("spine", "start_page", "end_page", "pages", "start_word", "end_word", "words"):
        value = getattr(item, name)
        if not 0 <= value <= _UINT16_MAX:
            raise _reject(f"{name} out of uint16 range")
    if item.para is not None and not 0 <= item.para <= _UINT16_MAX:
        raise _reject("para out of uint16 range")
    for name in ("created_at", "layout_signature"):
        if not 0 <= getattr(item, name) <= _UINT32_MAX:
            raise _reject(f"{name} out of uint32 range")
    if (item.start_offset is None) != (item.end_offset is None):
        raise _reject("start_offset and end_offset must be sent together")
    if item.start_offset is not None:
        # The device rejects an end offset of UINT32_MAX, which it uses as its
        # own "unset" marker.
        if not 0 <= item.start_offset < item.end_offset < _UINT32_MAX:
            raise _reject("start_offset must be below end_offset, and end_offset below UINT32_MAX")


# ------------------------------------------------------------------ #
# Store                                                                #
# ------------------------------------------------------------------ #

def _next_revision(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(revision), 0) + 1 FROM clippings").fetchone()
    return int(row[0])


def _apply(conn: sqlite3.Connection, document: str, item: ClippingItem) -> None:
    revision = _next_revision(conn)
    if item.deleted == 1:
        # Tombstones keep the row so the id still paginates out to other devices.
        conn.execute(
            """INSERT INTO clippings (revision, document, clip_id, deleted, text)
                    VALUES (?,?,?,1,'')
               ON CONFLICT(document, clip_id) DO UPDATE SET
                    revision = excluded.revision, deleted = 1, text = ''""",
            (revision, document, item.id),
        )
        return
    conn.execute(
        """INSERT INTO clippings
                (revision, document, clip_id, deleted, spine, start_page, end_page, pages,
                 start_word, end_word, words, para, chapter, text, created_at,
                 layout_signature, start_offset, end_offset)
                VALUES (?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(document, clip_id) DO UPDATE SET
                revision = excluded.revision, deleted = 0,
                spine = excluded.spine, start_page = excluded.start_page,
                end_page = excluded.end_page, pages = excluded.pages,
                start_word = excluded.start_word, end_word = excluded.end_word,
                words = excluded.words, para = excluded.para,
                chapter = excluded.chapter, text = excluded.text,
                created_at = excluded.created_at,
                layout_signature = excluded.layout_signature,
                start_offset = excluded.start_offset, end_offset = excluded.end_offset""",
        (revision, document, item.id, item.spine, item.start_page, item.end_page,
         item.pages, item.start_word, item.end_word, item.words, item.para,
         item.chapter or "", item.text, item.created_at, item.layout_signature,
         item.start_offset, item.end_offset),
    )


def _row_to_item(row: sqlite3.Row) -> dict:
    """Project a stored row to the wire shape the device decodes."""
    if row["deleted"]:
        return {"id": row["clip_id"], "deleted": 1, "revision": row["revision"]}
    item = {
        "id": row["clip_id"],
        "deleted": 0,
        "revision": row["revision"],
        "spine": row["spine"],
        "start_page": row["start_page"],
        "end_page": row["end_page"],
        "pages": row["pages"],
        "start_word": row["start_word"],
        "end_word": row["end_word"],
        "words": row["words"],
        "chapter": row["chapter"],
        "text": row["text"],
        "created_at": row["created_at"],
        "layout_signature": row["layout_signature"],
    }
    if row["para"] is not None:
        item["para"] = row["para"]
    if row["start_offset"] is not None and row["end_offset"] is not None:
        item["start_offset"] = row["start_offset"]
        item["end_offset"] = row["end_offset"]
    return item


# ------------------------------------------------------------------ #
# Sync endpoints (kosync-authenticated)                                #
# ------------------------------------------------------------------ #

sync_router = APIRouter(prefix="/api/v1/clippings", tags=["clippings"])


@sync_router.get("/{document:path}")
async def list_clippings(
    document: str,
    cursor: int = 0,
    limit: int = Query(default=100, ge=1, le=200),
    format: str = "reader",
):
    """Page clippings newer than `cursor`, oldest revision first."""
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clippings WHERE document = ? AND revision > ? "
            "ORDER BY revision LIMIT ?",
            (document, cursor, limit + 1),
        ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "sync_version": SYNC_VERSION,
        # Holding the cursor on an empty page keeps it from ever moving
        # backwards, which the device treats as a protocol error.
        "cursor": rows[-1]["revision"] if rows else cursor,
        "more": more,
        "items": [_row_to_item(r) for r in rows],
    }


@sync_router.put("/{document:path}")
async def upload_clippings(document: str, body: ClippingUpload):
    """Accept uploaded clippings and tombstones for one book."""
    init_db()
    for item in body.items:
        _validate(item)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for item in body.items:
            _apply(conn, document, item)
    logger.info("Clippings: accepted %d item(s) for %s", len(body.items), document[:12])
    return {"sync_version": SYNC_VERSION, "accepted": 1}


# ------------------------------------------------------------------ #
# UI endpoints                                                         #
# ------------------------------------------------------------------ #

ui_router = APIRouter(tags=["clippings"])


def _slug_by_hash() -> dict[str, str]:
    """Document hash → canonical book slug, from the alias table."""
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT hash, title FROM document_aliases").fetchall()
    except Exception:
        return {}
    return {r["hash"]: canonical_book_slug(r["title"]) for r in rows}


def _titles_by_hash() -> dict[str, str]:
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT hash, title FROM document_aliases").fetchall()
    except Exception:
        return {}
    return {r["hash"]: r["title"] for r in rows}


def _decorate(rows: list[sqlite3.Row]) -> list[dict]:
    titles = _titles_by_hash()
    out = []
    for row in rows:
        clip = dict(row)
        clip["book_title"] = titles.get(row["document"], "")
        out.append(clip)
    return out


@ui_router.get("/api/clippings")
async def all_clippings(limit: int = Query(default=200, ge=1, le=1000)):
    """Every live clipping, newest first, with its book title resolved."""
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clippings WHERE deleted = 0 ORDER BY revision DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return _decorate(rows)


@ui_router.get("/api/books/{slug}/clippings")
async def book_clippings(slug: str):
    """Clippings for a book, across every document hash that resolves to it."""
    init_db()
    wanted = canonical_book_slug(slug)
    hashes = [h for h, s in _slug_by_hash().items() if s == wanted]
    if not hashes:
        return []
    placeholders = ",".join("?" * len(hashes))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM clippings WHERE deleted = 0 AND document IN ({placeholders}) "
            "ORDER BY spine, start_page, start_word",
            hashes,
        ).fetchall()
    return _decorate(rows)


@ui_router.delete("/api/clippings/{clipping_id}")
async def delete_clipping(clipping_id: int):
    """Tombstone a clipping so devices remove it on their next sync."""
    init_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT document, clip_id FROM clippings WHERE id = ?", (clipping_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Clipping not found")
        conn.execute(
            "UPDATE clippings SET deleted = 1, text = '', revision = ? WHERE id = ?",
            (_next_revision(conn), clipping_id),
        )
    logger.info("Clipping tombstoned: %d", clipping_id)
    return {"deleted": clipping_id}
