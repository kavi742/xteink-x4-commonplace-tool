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

from xteink_service import search_index, undo
from xteink_service.booktitle import canonical_book_slug
from xteink_service.clip_match import attach_screenshot_ids
from xteink_service.content_kind import ARTICLES_SLUG, alias_rows
from xteink_service.state import (
    citation_clause,
    deleted_citation_ids,
    mark_citations,
    restore_citations,
)

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
                deleted_at       TIMESTAMP,
                deleted_source   TEXT    NOT NULL DEFAULT '',
                trashed_text     TEXT    NOT NULL DEFAULT '',
                UNIQUE(document, clip_id)
            )
        """)
        # A tombstone clears `text` so a deleted clipping is never paginated back
        # down. `trashed_text` is the only reason a restore is possible at all.
        for col, typedef in [
            ("deleted_at", "TIMESTAMP"),
            ("deleted_source", "TEXT NOT NULL DEFAULT ''"),
            ("trashed_text", "TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE clippings ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_clippings_doc_rev "
            "ON clippings (document, revision)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_clippings_trash "
            "ON clippings (deleted_at) WHERE deleted = 1"
        )
        undo.init_db(conn)
        search_index.init_db(conn)


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


def delete_label(source: str, count: int, ref_count: int) -> str:
    """The whole toast line. The device wording differs because undoing it talks
    back to the reader, which is a change the user is making to another device."""
    noun = "clipping" if count == 1 else "clippings"
    if source == "device":
        line = f"The reader deleted {count} {noun}. Undo sends it back at the next sync."
    else:
        line = f"Deleted {count} {noun}"
    return line + citation_clause(ref_count)


def _apply(conn: sqlite3.Connection, document: str, item: ClippingItem) -> dict | None:
    """Write one uploaded item. Returns the entry to log when a live clipping dies."""
    revision = _next_revision(conn)
    prior = conn.execute(
        "SELECT id, deleted FROM clippings WHERE document = ? AND clip_id = ?",
        (document, item.id),
    ).fetchone()
    if item.deleted == 1:
        # Tombstones keep the row so the id still paginates out to other devices.
        # `clippings.text` on the right-hand side is still the pre-update value,
        # which is the one chance to save the body before it is cleared.
        conn.execute(
            """INSERT INTO clippings (revision, document, clip_id, deleted, text,
                                      deleted_at, deleted_source, trashed_text)
                    VALUES (?,?,?,1,'',CURRENT_TIMESTAMP,'device','')
               ON CONFLICT(document, clip_id) DO UPDATE SET
                    revision = excluded.revision, deleted = 1,
                    trashed_text = COALESCE(NULLIF(clippings.text, ''), clippings.trashed_text),
                    text = '', deleted_at = CURRENT_TIMESTAMP, deleted_source = 'device'""",
            (revision, document, item.id),
        )
        _reindex(conn, document, item.id, live=False)
        if prior is None or prior["deleted"]:
            return None  # nothing live was lost, so no toast and nothing to undo
        return {
            "id": prior["id"], "document": document,
            "clip_id": item.id, "delete_revision": revision,
        }
    # A device re-uploading a clipping is an implicit restore.
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
                start_offset = excluded.start_offset, end_offset = excluded.end_offset,
                deleted_at = NULL, deleted_source = '', trashed_text = ''""",
        (revision, document, item.id, item.spine, item.start_page, item.end_page,
         item.pages, item.start_word, item.end_word, item.words, item.para,
         item.chapter or "", item.text, item.created_at, item.layout_signature,
         item.start_offset, item.end_offset),
    )
    _reindex(conn, document, item.id, live=True)
    return None


def _reindex(conn: sqlite3.Connection, document: str, clip_id: str, live: bool) -> None:
    """Keep search in step with a written clipping. A stale index must not fail a sync."""
    try:
        row = conn.execute(
            "SELECT id FROM clippings WHERE document = ? AND clip_id = ?",
            (document, clip_id),
        ).fetchone()
        if not row:
            return
        if live:
            search_index.index_clipping(conn, row[0])
        else:
            search_index.unindex(conn, "clipping", row[0])
    except sqlite3.OperationalError as exc:
        logger.warning("Search index update skipped: %s", exc)


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
        tombstoned = [entry for item in body.items
                      if (entry := _apply(conn, document, item)) is not None]
        if tombstoned:
            # One entry per PUT, so a sync that drops seven clippings is one toast.
            refs: list[int] = []
            for entry in tombstoned:
                refs.extend(mark_citations(conn, "clipping", entry["id"]))
            undo.record(
                "clipping_delete",
                delete_label("device", len(tombstoned), len(refs)),
                {"deleted_source": "device", "clippings": tombstoned, "writing_refs": refs},
                conn=conn,
            )
    logger.info("Clippings: accepted %d item(s) for %s", len(body.items), document[:12])
    return {"sync_version": SYNC_VERSION, "accepted": 1}


# ------------------------------------------------------------------ #
# UI endpoints                                                         #
# ------------------------------------------------------------------ #

ui_router = APIRouter(tags=["clippings"])


def _aliases() -> list[tuple[str, str, str]]:
    """``(hash, title, kind)`` for every alias, or an empty list if unavailable."""
    try:
        with _connect() as conn:
            return alias_rows(conn)
    except Exception:
        return []


def hashes_for_slug(slug: str, kind: str = "book") -> list[str]:
    """Document hashes of one kind whose title resolves to ``slug``.

    Articles are slugged in article mode so two unrelated articles cannot share
    a slug and swap screenshot links; see ``booktitle``.
    """
    target = canonical_book_slug(slug, kind)
    if not target:
        return []
    return [
        h for h, t, k in _aliases()
        if k == kind and canonical_book_slug(t, k) == target
    ]


def _decorate(rows: list[sqlite3.Row]) -> list[dict]:
    """Add the resolved book title, content kind, and matching screenshot id."""
    aliases = {h: (t, kind) for h, t, kind in _aliases()}
    out = []
    for row in rows:
        clip = dict(row)
        title, kind = aliases.get(row["document"], ("", "book"))
        clip["book_title"] = title
        clip["kind"] = kind
        out.append(clip)
    slugs = {h: canonical_book_slug(t, kind) for h, (t, kind) in aliases.items()}
    with _connect() as conn:
        attach_screenshot_ids(conn, out, slugs)
    return out


def clipping_counts_by_document() -> dict[str, int]:
    """Live (non-tombstoned) clipping count per document hash."""
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT document, COUNT(*) AS n FROM clippings "
            "WHERE deleted = 0 GROUP BY document"
        ).fetchall()
    return {r["document"]: r["n"] for r in rows}


def clippings_for_documents(hashes: list[str]) -> list[dict]:
    """Live clippings for a set of document hashes, in reading order."""
    init_db()
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
    if canonical_book_slug(slug) == ARTICLES_SLUG:
        return []
    return clippings_for_documents(hashes_for_slug(slug, "book"))


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
        revision = _next_revision(conn)
        conn.execute(
            "UPDATE clippings SET deleted = 1, "
            "trashed_text = COALESCE(NULLIF(text, ''), trashed_text), text = '', "
            "deleted_at = CURRENT_TIMESTAMP, deleted_source = 'ui', revision = ? "
            "WHERE id = ?",
            (revision, clipping_id),
        )
        try:
            search_index.unindex(conn, "clipping", clipping_id)
        except sqlite3.OperationalError as exc:
            logger.warning("Search index update skipped: %s", exc)
        refs = mark_citations(conn, "clipping", clipping_id)
        token = undo.record(
            "clipping_delete",
            delete_label("ui", 1, len(refs)),
            {
                "deleted_source": "ui",
                "clippings": [{
                    "id": clipping_id, "document": row["document"],
                    "clip_id": row["clip_id"], "delete_revision": revision,
                }],
                "writing_refs": refs,
            },
            conn=conn,
        )
    logger.info("Clipping tombstoned: %d", clipping_id)
    return {
        "deleted": clipping_id, "soft": True, "revision": revision,
        "writing_refs_marked": len(refs), "undo_token": token,
    }


@ui_router.post("/api/clippings/{clipping_id}/restore")
async def restore_clipping(clipping_id: int):
    """Bring a trashed clipping back. The reader picks it up at its next sync."""
    init_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute(
            "SELECT 1 FROM clippings WHERE id = ?", (clipping_id,)
        ).fetchone():
            raise HTTPException(status_code=404, detail="Clipping not found")
        restored = _restore_clipping(conn, clipping_id)
        if restored is None:
            raise HTTPException(
                status_code=409,
                detail="Clipping text was discarded by an older tombstone "
                       "and cannot be restored",
            )
        refs = restore_citations(
            conn, deleted_citation_ids(conn, "clipping", clipping_id)
        )
    logger.info("Clipping restored: %d at revision %d", clipping_id, restored["revision"])
    return {
        "restored": clipping_id, "revision": restored["revision"],
        "writing_refs_restored": refs,
    }


def _restore_clipping(conn: sqlite3.Connection, clipping_id: int) -> dict | None:
    """Un-tombstone one clipping on an open transaction, or None if its body is gone.

    The fresh revision is mandatory. Reusing the old one leaves the row below
    every cursor the device has already passed, so the reader never downloads it
    again and the clipping stays missing even though the server believes it back.
    """
    row = conn.execute(
        "SELECT trashed_text FROM clippings WHERE id = ?", (clipping_id,)
    ).fetchone()
    if row is None or not row["trashed_text"]:
        return None
    revision = _next_revision(conn)
    conn.execute(
        "UPDATE clippings SET deleted = 0, text = trashed_text, trashed_text = '', "
        "deleted_at = NULL, deleted_source = '', revision = ? WHERE id = ?",
        (revision, clipping_id),
    )
    try:
        search_index.index_clipping(conn, clipping_id)
    except sqlite3.OperationalError as exc:
        logger.warning("Search index update skipped: %s", exc)
    return {"id": clipping_id, "revision": revision}


@undo.register("clipping_delete")
def _undo_clipping_delete(conn: sqlite3.Connection, payload: dict) -> dict:
    restored: list[int] = []
    skipped: list[int] = []
    revision = 0
    for entry in payload.get("clippings", []):
        done = _restore_clipping(conn, entry["id"])
        if done is None:
            skipped.append(entry["id"])  # purged or emptied while the token lived
            continue
        restored.append(entry["id"])
        revision = max(revision, done["revision"])
    return {
        "restored": restored,
        "revision": revision,
        "writing_refs_restored": restore_citations(conn, payload.get("writing_refs", [])),
        "skipped": skipped,
    }


def list_trashed_clippings() -> list[dict]:
    """Tombstoned clippings, newest first. Pre-1.3 tombstones sort last."""
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, clip_id, document, chapter, trashed_text, deleted_at, deleted_source "
            "FROM clippings WHERE deleted = 1 "
            "ORDER BY deleted_at IS NULL, deleted_at DESC, id DESC"
        ).fetchall()
    aliases = {h: (t, kind) for h, t, kind in _aliases()}
    out = []
    for row in rows:
        title, kind = aliases.get(row["document"], ("", "book"))
        out.append({
            "id": row["id"],
            "clip_id": row["clip_id"],
            "document": row["document"],
            "book_title": title,
            "book_slug": canonical_book_slug(title, kind) if title else "",
            "chapter": row["chapter"],
            "text": row["trashed_text"],
            "deleted_at": row["deleted_at"],
            "deleted_source": row["deleted_source"],
            "restorable": bool(row["trashed_text"]),
        })
    return out


def purge_clipping(clipping_id: int) -> bool:
    """Clear the saved body. The tombstone row stays, or the device resurrects the id."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE clippings SET trashed_text = '' WHERE id = ? AND deleted = 1",
            (clipping_id,),
        )
    return cur.rowcount > 0


def purge_trashed_clippings() -> int:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE clippings SET trashed_text = '' WHERE deleted = 1 AND trashed_text != ''"
        )
    return cur.rowcount
