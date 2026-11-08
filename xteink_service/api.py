"""
Phase 8 + 9 API endpoints.

Mounted on the koreader_sync FastAPI app (port 8090):

  GET  /status                               — service health snapshot
  GET  /api/books                            — book list with screenshot counts
  GET  /api/books/{slug}/screenshots         — screenshots for a book
  GET  /api/screenshots/{id}                 — single screenshot metadata
  GET  /api/screenshots/{id}/image     — serve PNG from vault filesystem
  PUT  /api/screenshots/{id}           — update ocr_corrected / user_notes
  GET  /api/reading-log                — KOReader progress history (with titles)
  GET  /api/devices                    — KOReader sync clients (X4, Kindle, …)
  GET  /api/aliases                    — hash → title table
  PUT  /api/aliases/{hash}             — set/update a book title alias
  POST /api/vault/rebuild              — rebuild all vault markdown from DB

Requires STATE_DB, KOREADER_DB, VAULT_PATH env vars (defaulting to /data/*).
"""
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from xteink_service import export, logbuffer, resurface, search_index, undo
from xteink_service.booktitle import canonical_book_slug
from xteink_service.content_kind import (
    ARTICLE,
    ARTICLES_SLUG,
    ARTICLES_TITLE,
    alias_rows,
    kind_for_vault_root,
    kinds_by_hash,
)
from xteink_service.state import (
    SyncState,
    citation_clause,
    deleted_citation_ids,
    mark_citations,
    restore_citations,
)
from xteink_service.vault_writer import VaultWriter

logger = logging.getLogger(__name__)
router = APIRouter()

# A reading position at or beyond this fraction counts the book as finished.
FINISHED = 0.95

# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _state_db() -> str:
    return os.getenv("STATE_DB", "/data/state/state.db")


def _koreader_db() -> str:
    return os.getenv("KOREADER_DB", "/data/state/koreader.db")


def _vault_path() -> Path:
    return Path(os.getenv("VAULT_PATH", "/data/vault"))


def _state_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_state_db())
    conn.row_factory = sqlite3.Row
    return conn


def _koreader_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_koreader_db())
    conn.row_factory = sqlite3.Row
    return conn


def _pages_by_hash() -> dict[str, dict]:
    """Map each KOReader document hash to its book's cached page count.

    Joins document_aliases (hash -> title) with book_pages (title -> total_pages)
    so reading percentages can be turned into page numbers. Returns
    ``{hash: {"total_pages": int, "source": str}}``; empty if either table is
    missing (e.g. no page lookup has run yet).
    """
    try:
        from xteink_service.book_pages import get_page_counts
        counts = get_page_counts(_state_db())
        if not counts:
            return {}
        out: dict[str, dict] = {}
        with _state_conn() as conn:
            for row in conn.execute("SELECT hash, title FROM document_aliases"):
                info = counts.get(row["title"])
                if info:
                    out[row["hash"]] = info
        return out
    except Exception:
        return {}


def _vault_root_of(row) -> str:
    """Top-level vault folder a screenshot was written to.

    Rows archived before articles existed have no value here and live under
    Books, which is also where an unmigrated database lands.
    """
    return dict(row).get("vault_root") or "Books"


def _ensure_alias_kind(conn: sqlite3.Connection) -> None:
    """Add document_aliases.kind if this database predates it (migration-safe)."""
    try:
        conn.execute("ALTER TABLE document_aliases ADD COLUMN kind TEXT DEFAULT 'book'")
    except sqlite3.OperationalError:
        pass  # column already exists


def _articles_index(conn: sqlite3.Connection) -> dict[str, dict]:
    """Article slug -> ``{"title", "hashes"}`` for aliases marked as articles."""
    index: dict[str, dict] = {}
    for doc_hash, title, kind in alias_rows(conn):
        if kind != ARTICLE or not title:
            continue
        slug = canonical_book_slug(title, ARTICLE)
        if not slug:
            continue
        index.setdefault(slug, {"title": title, "hashes": []})["hashes"].append(doc_hash)
    return index


def _article_titles_for_slug(conn: sqlite3.Connection, slug: str) -> list[str]:
    """Distinct screenshot folder names belonging to one article."""
    return [
        r["book_title"]
        for r in conn.execute("SELECT DISTINCT book_title FROM synced_screenshots")
        if r["book_title"] and canonical_book_slug(r["book_title"], ARTICLE) == slug
    ]


def _screenshot_rows(conn: sqlite3.Connection, titles: list[str]) -> list[dict]:
    """Screenshot rows for a set of titles, one per stitched image.

    Stitched screenshots write ONE image but mark each constituent page as its
    own row (all sharing one vault_png_path, needed for sync dedup). Only the
    lowest-id row of each group is returned so the same image isn't listed once
    per page.
    """
    if not titles:
        return []
    ph = ",".join("?" for _ in titles)
    rows = conn.execute(
        f"""
        SELECT * FROM synced_screenshots
        WHERE book_title IN ({ph})
          AND id IN (
              SELECT MIN(id) FROM synced_screenshots
              WHERE book_title IN ({ph})
              GROUP BY COALESCE(vault_png_path, CAST(id AS TEXT))
          )
        ORDER BY id
        """,
        titles + titles,
    ).fetchall()
    return [dict(r) for r in rows]


def _collect_articles() -> list[dict]:
    """Every article that has any activity: screenshots, clippings, or progress.

    An alias on its own only means the file sits on the card, so those are left
    out for the same reason unread books are left out of ``/api/books``.
    """
    try:
        with _state_conn() as conn:
            index = _articles_index(conn)
            if not index:
                return []
            shot_rows = conn.execute("""
                SELECT book_title,
                       COUNT(DISTINCT COALESCE(vault_png_path, CAST(id AS TEXT))) AS screenshot_count,
                       MAX(synced_at) AS last_synced,
                       MAX(sync_date) AS last_date
                FROM synced_screenshots
                GROUP BY book_title
            """).fetchall()
    except Exception:
        return []

    shots: dict[str, dict] = {}
    for r in shot_rows:
        slug = canonical_book_slug(r["book_title"] or "", ARTICLE)
        if slug not in index:
            continue
        agg = shots.setdefault(
            slug, {"screenshot_count": 0, "last_synced": None, "last_date": None}
        )
        agg["screenshot_count"] += r["screenshot_count"]
        for key in ("last_synced", "last_date"):
            if r[key] and (agg[key] is None or r[key] > agg[key]):
                agg[key] = r[key]

    try:
        with _koreader_conn() as conn:
            progress = {
                r["document"]: r for r in conn.execute(
                    "SELECT document, MAX(timestamp) AS last_read_at, "
                    "MAX(percentage) AS pct FROM progress_updates GROUP BY document"
                )
            }
    except Exception:
        progress = {}

    try:
        from xteink_service.clippings import clipping_counts_by_document
        clip_counts = clipping_counts_by_document()
    except Exception:
        clip_counts = {}

    from xteink_service.pages import page_at
    pages_by_hash = _pages_by_hash()

    out: list[dict] = []
    for slug, entry in index.items():
        hashes = entry["hashes"]
        agg = shots.get(slug, {})
        screenshot_count = agg.get("screenshot_count", 0)
        clipping_count = sum(clip_counts.get(h, 0) for h in hashes)
        read = [progress[h] for h in hashes if h in progress]
        if not read and not screenshot_count and not clipping_count:
            continue
        pct = max((r["pct"] for r in read), default=None)
        info = next((pages_by_hash[h] for h in hashes if h in pages_by_hash), None)
        total_pages = info["total_pages"] if info else None
        out.append({
            "slug": slug,
            "title": entry["title"],
            "hashes": hashes,
            "screenshot_count": screenshot_count,
            "clipping_count": clipping_count,
            "percentage_display": round(pct * 100, 1) if pct is not None else None,
            "last_read_at": max((r["last_read_at"] for r in read), default=None),
            "finished": pct is not None and pct >= FINISHED,
            "total_pages": total_pages,
            "page": (
                page_at(pct, total_pages)
                if pct is not None and total_pages else None
            ),
            "last_synced": agg.get("last_synced"),
            "last_date": agg.get("last_date"),
        })

    out.sort(key=lambda a: (
        a["last_read_at"] is None, -(a["last_read_at"] or 0), a["title"].lower()
    ))
    return out


_ARTICLE_FIELDS = (
    "slug", "title", "hashes", "screenshot_count", "clipping_count",
    "percentage_display", "last_read_at", "finished", "total_pages", "page",
)


def _book_titles_for_slug(conn: sqlite3.Connection, slug: str) -> list[str]:
    """Distinct screenshot book_titles whose canonical slug matches ``slug``."""
    target = canonical_book_slug(slug)
    if target == ARTICLES_SLUG:
        return []
    article_slugs = set(_articles_index(conn))
    return [
        r["book_title"]
        for r in conn.execute("SELECT DISTINCT book_title FROM synced_screenshots")
        if r["book_title"] and canonical_book_slug(r["book_title"]) == target
        and canonical_book_slug(r["book_title"], ARTICLE) not in article_slugs
    ]


def _hashes_for_book(slug: str) -> list[str]:
    """KOReader document hashes for a book, matched by canonical slug.

    The screenshot folder name ("The-Hidden-Keys") and the Calibre alias title
    ("Hidden Keys, The - Andre Alexis") canonicalise to the same slug, so a
    book's screenshots and reading progress resolve under one URL. Articles are
    excluded: they are served by ``/api/articles`` instead.
    """
    target = canonical_book_slug(slug)
    if target == ARTICLES_SLUG:
        return []
    try:
        with _state_conn() as conn:
            return [
                h for h, title, kind in alias_rows(conn)
                if kind != ARTICLE and canonical_book_slug(title) == target
            ]
    except Exception:
        return []


# ------------------------------------------------------------------ #
# Phase 8 — /status                                                    #
# ------------------------------------------------------------------ #

@router.get("/status")
async def status():
    """Service health snapshot — last sync, book counts, recent KOReader updates."""
    today = date.today().isoformat()
    result: dict = {}

    # Screenshot stats from state.db
    try:
        with _state_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(vault_png_path, CAST(id AS TEXT))) FROM synced_screenshots"
            ).fetchone()[0]
            last_row = conn.execute(
                "SELECT synced_at, book_title FROM synced_screenshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            books_today = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT book_title FROM synced_screenshots WHERE sync_date = ?",
                    (today,),
                ).fetchall()
            ]
            today_count = conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(vault_png_path, CAST(id AS TEXT))) FROM synced_screenshots WHERE sync_date = ?",
                (today,),
            ).fetchone()[0]
        result["screenshots"] = {
            "total": total,
            "last_sync_at": last_row["synced_at"] if last_row else None,
            "last_book": last_row["book_title"] if last_row else None,
            "today_count": today_count,
            "books_today": books_today,
            "noted_count": conn.execute(
                "SELECT COUNT(*) FROM synced_screenshots WHERE user_notes IS NOT NULL AND user_notes != ''"
            ).fetchone()[0],
        }
    except Exception as exc:
        result["screenshots"] = {"error": str(exc)}

    # KOReader stats
    try:
        with _koreader_conn() as conn:
            total_k = conn.execute(
                "SELECT COUNT(*) FROM progress_updates"
            ).fetchone()[0]
            recent = conn.execute(
                "SELECT document, percentage, timestamp FROM progress_updates ORDER BY id DESC LIMIT 5"
            ).fetchall()
        # Resolve hashes to titles
        aliases: dict[str, str] = {}
        try:
            with _state_conn() as conn:
                for row in conn.execute("SELECT hash, title FROM document_aliases"):
                    aliases[row["hash"]] = row["title"]
        except Exception:
            pass
        result["koreader"] = {
            "total_updates": total_k,
            "recent": [
                {
                    "document": r["document"],
                    "title": aliases.get(r["document"], r["document"][:12] + "..."),
                    "percentage": round(r["percentage"] * 100, 1),
                    "at": datetime.fromtimestamp(r["timestamp"], tz=timezone.utc).isoformat(),
                }
                for r in recent
            ],
        }
    except Exception as exc:
        result["koreader"] = {"error": str(exc)}

    return result


# ------------------------------------------------------------------ #
# Phase 9 — Books                                                      #
# ------------------------------------------------------------------ #

@router.get("/api/books")
async def list_books():
    """List every book, whether it arrived as screenshots or as reading progress.

    A book read on a device that takes no screenshots (a Kindle, say) exists
    only in the reading log, so screenshots alone would hide it. The two sides
    are merged on canonical slug — the same book must not appear twice under its
    screenshot folder name and its alias title.

    Only aliases that actually have progress count: the File Transfer preload
    maps every epub on the device, and listing all of those would bury the books
    actually being read.

    Articles never appear individually. They collapse into one "Articles" entry
    whose detail lives behind /api/articles.
    """
    articles = _collect_articles()
    article_slugs = {a["slug"] for a in articles}

    books: dict[str, dict] = {}
    titles: dict[str, str] = {}
    kinds: dict[str, str] = {}
    try:
        with _state_conn() as conn:
            for h, title, kind in alias_rows(conn):
                titles[h] = title
                kinds[h] = kind
            for r in conn.execute("""
                SELECT book_title,
                       COUNT(DISTINCT COALESCE(vault_png_path, CAST(id AS TEXT))) AS screenshot_count,
                       MAX(synced_at)  AS last_synced,
                       MAX(sync_date)  AS last_date
                FROM synced_screenshots
                GROUP BY book_title
            """):
                if not r["book_title"]:
                    continue
                if canonical_book_slug(r["book_title"], ARTICLE) in article_slugs:
                    continue
                slug = canonical_book_slug(r["book_title"])
                books[slug] = {
                    **dict(r), "slug": slug, "kind": "book",
                    "percentage_display": None, "last_read_at": None,
                }
    except Exception:
        pass

    try:
        with _koreader_conn() as conn:
            progress = conn.execute("""
                SELECT document, MAX(timestamp) AS last_read_at,
                       MAX(percentage) AS pct
                FROM progress_updates GROUP BY document
            """).fetchall()
    except Exception:
        progress = []

    for r in progress:
        title = titles.get(r["document"])
        if not title or kinds.get(r["document"]) == ARTICLE:
            continue
        slug = canonical_book_slug(title)
        entry = books.get(slug)
        if entry is None:
            entry = books[slug] = {
                "book_title": title,
                "slug": slug,
                "kind": "book",
                "screenshot_count": 0,
                "last_synced": None,
                "last_date": None,
                "percentage_display": None,
                "last_read_at": None,
            }
        # A book split across devices has one row per hash; keep the latest.
        if entry["last_read_at"] is None or r["last_read_at"] > entry["last_read_at"]:
            entry["last_read_at"] = r["last_read_at"]
            entry["percentage_display"] = round(r["pct"] * 100, 1)

    if articles:
        books[ARTICLES_SLUG] = {
            "book_title": ARTICLES_TITLE,
            "slug": ARTICLES_SLUG,
            "kind": "articles",
            "screenshot_count": sum(a["screenshot_count"] for a in articles),
            "article_count": len(articles),
            "last_synced": max((a["last_synced"] for a in articles if a["last_synced"]),
                               default=None),
            "last_date": max((a["last_date"] for a in articles if a["last_date"]),
                             default=None),
            "percentage_display": None,
            "last_read_at": max(
                (a["last_read_at"] for a in articles if a["last_read_at"] is not None),
                default=None,
            ),
        }

    return sorted(books.values(), key=lambda b: b["book_title"].lower())


# ------------------------------------------------------------------ #
# Articles                                                             #
# ------------------------------------------------------------------ #

@router.get("/api/articles")
async def list_articles():
    """Every article behind the collapsed "Articles" entry in /api/books."""
    return [{k: a[k] for k in _ARTICLE_FIELDS} for a in _collect_articles()]


@router.get("/api/articles/{slug}")
async def get_article(slug: str):
    """One article with its own screenshots and clippings."""
    article = next((a for a in _collect_articles() if a["slug"] == slug), None)
    if article is None:
        raise HTTPException(status_code=404, detail=f"Article '{slug}' not found")

    try:
        with _state_conn() as conn:
            screenshots = _screenshot_rows(
                conn, _article_titles_for_slug(conn, slug)
            )
    except Exception:
        screenshots = []

    from xteink_service.clippings import clippings_for_documents
    detail = {k: article[k] for k in _ARTICLE_FIELDS
              if k not in ("screenshot_count", "clipping_count")}
    return {
        **detail,
        "screenshots": screenshots,
        "clippings": clippings_for_documents(article["hashes"]),
    }


@router.get("/api/books/{slug}/screenshots")
async def list_screenshots(slug: str):
    """Screenshots for a book.

    Matches the book by its canonical slug (see ``booktitle``), so the screenshot
    folder name and a Calibre-style alias title resolve to the same book.
    """
    try:
        with _state_conn() as conn:
            rows = _screenshot_rows(conn, _book_titles_for_slug(conn, slug))
        if not rows:
            raise HTTPException(status_code=404, detail=f"Book '{slug}' not found")
        return rows
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail=f"Book '{slug}' not found")


@router.get("/api/books/{slug}/reading-calendar")
async def reading_calendar(slug: str):
    """Per-day reading activity for a book — powers the book-page calendar.

    Resolves the book title (slug) to its KOReader document hash(es) via the
    alias table, groups that book's progress updates by local calendar day, and
    reports how much was read each day (forward progress since the previous
    synced day; the first synced day is measured from the book's start), the
    day's end position, and how many sync sessions occurred.
    """
    hashes = _hashes_for_book(slug)
    if not hashes:
        return []

    placeholders = ",".join("?" for _ in hashes)
    with _koreader_conn() as conn:
        rows = conn.execute(
            f"SELECT timestamp, percentage FROM progress_updates "
            f"WHERE document IN ({placeholders})",
            hashes,
        ).fetchall()
    if not rows:
        return []

    # Group percentages by local calendar day (matches the vault's local dates).
    by_day: dict[str, list[float]] = {}
    for r in rows:
        day = datetime.fromtimestamp(r["timestamp"]).date().isoformat()
        by_day.setdefault(day, []).append(r["percentage"])

    from xteink_service.pages import page_at
    pages_by_hash = _pages_by_hash()
    info = next((pages_by_hash[h] for h in hashes if h in pages_by_hash), None)
    total_pages = info["total_pages"] if info else 0

    out = []
    prev_end = 0.0
    for i, day in enumerate(sorted(by_day)):
        pcts = by_day[day]
        day_max = max(pcts)
        start = 0.0 if i == 0 else prev_end
        read = max(0.0, day_max - start)
        out.append({
            "date": day,
            "percent_read": round(read * 100, 1),
            "start_pct": round(start * 100, 1),
            "end_pct": round(day_max * 100, 1),
            "sessions": len(pcts),
            "end_page": page_at(day_max, total_pages) if total_pages else None,
            "pages_read": (
                page_at(day_max, total_pages) - page_at(start, total_pages)
                if total_pages else None
            ),
        })
        prev_end = max(prev_end, day_max)
    return out


@router.get("/api/books/{slug}/reading-stats")
async def book_reading_stats(slug: str):
    """Per-book reading summary: current position + page estimate.

    Resolves the book title to its KOReader hash(es), takes the furthest synced
    position, and turns it into a page number using the cached page count
    (Open Library, or a word-count estimate). ``total_pages``/``current_page``
    are null when no page count is available yet.
    """
    hashes = _hashes_for_book(slug)
    if not hashes:
        return {
            "total_pages": None, "page_source": None, "current_pct": 0.0,
            "current_page": None, "sessions": 0, "days_read": 0, "finished": False,
        }

    placeholders = ",".join("?" for _ in hashes)
    with _koreader_conn() as conn:
        rows = conn.execute(
            f"SELECT timestamp, percentage FROM progress_updates "
            f"WHERE document IN ({placeholders})",
            hashes,
        ).fetchall()

    from xteink_service.pages import page_at
    pages_by_hash = _pages_by_hash()
    info = next((pages_by_hash[h] for h in hashes if h in pages_by_hash), None)
    total_pages = info["total_pages"] if info else None

    current = max((r["percentage"] for r in rows), default=0.0)
    days = {datetime.fromtimestamp(r["timestamp"]).date().isoformat() for r in rows}

    return {
        "total_pages": total_pages,
        "page_source": info["source"] if info else None,
        "current_pct": round(current * 100, 1),
        "current_page": page_at(current, total_pages) if total_pages else None,
        "sessions": len(rows),
        "days_read": len(days),
        "finished": current >= 0.95,
    }


# ------------------------------------------------------------------ #
# Phase 9 — Screenshots                                                #
# ------------------------------------------------------------------ #

@router.get("/api/screenshots/{screenshot_id}")
async def get_screenshot(screenshot_id: int):
    """Single screenshot metadata."""
    with _state_conn() as conn:
        row = conn.execute(
            "SELECT * FROM synced_screenshots WHERE id = ?", (screenshot_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return dict(row)


@router.get("/api/screenshots/{screenshot_id}/image")
async def get_screenshot_image(screenshot_id: int):
    """Serve the PNG from the vault filesystem."""
    with _state_conn() as conn:
        row = conn.execute(
            "SELECT * FROM synced_screenshots WHERE id = ?",
            (screenshot_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    vault_png_path = row["vault_png_path"]
    if not vault_png_path:
        raise HTTPException(status_code=404, detail="No vault path stored for this screenshot")

    png = _vault_path() / _vault_root_of(row) / vault_png_path
    if not png.exists():
        raise HTTPException(status_code=404, detail=f"File not found on disk: {png}")

    return FileResponse(str(png), media_type="image/png")


class ScreenshotUpdate(BaseModel):
    ocr_corrected: str | None = None
    user_notes: str | None = None


@router.put("/api/screenshots/{screenshot_id}")
async def update_screenshot(screenshot_id: int, body: ScreenshotUpdate):
    """Update OCR correction and/or user notes for a screenshot."""
    fields, values = [], []
    if body.ocr_corrected is not None:
        fields.append("ocr_corrected = ?")
        values.append(body.ocr_corrected)
    if body.user_notes is not None:
        fields.append("user_notes = ?")
        values.append(body.user_notes)
    if not fields:
        raise HTTPException(status_code=400, detail="Provide ocr_corrected or user_notes")

    values.append(screenshot_id)
    with sqlite3.connect(_state_db()) as conn:
        result = conn.execute(
            f"UPDATE synced_screenshots SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Screenshot not found")
        # This endpoint writes without going through SyncState, so it owns its
        # own index maintenance.
        try:
            search_index.index_screenshot(conn, screenshot_id)
            search_index.index_note(conn, screenshot_id)
        except sqlite3.OperationalError as exc:
            logger.warning("Search index update skipped: %s", exc)

    logger.info("Updated screenshot %d: %s", screenshot_id, ', '.join(fields))
    return {"updated": screenshot_id}


# ------------------------------------------------------------------ #
# Phase 9 — Reading log                                               #
# ------------------------------------------------------------------ #

@router.get("/api/reading-log")
async def reading_log(limit: int = 100):
    """Recent KOReader progress updates with resolved titles."""
    with _koreader_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM progress_updates ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        # The entry that finished each book = the earliest update that reached
        # the finished threshold (min id, since ids are insertion-ordered).
        finishing = {
            r["document"]: r["fid"]
            for r in conn.execute(
                "SELECT document, MIN(id) AS fid FROM progress_updates "
                "WHERE percentage >= ? GROUP BY document",
                (FINISHED,),
            )
        }

    aliases: dict[str, str] = {}
    screenshot_by_canon: dict[str, str] = {}
    try:
        with _state_conn() as conn:
            for row in conn.execute("SELECT hash, title FROM document_aliases"):
                aliases[row["hash"]] = row["title"]
            for row in conn.execute("SELECT DISTINCT book_title FROM synced_screenshots"):
                bt = row["book_title"]
                if bt:
                    screenshot_by_canon.setdefault(canonical_book_slug(bt), bt)
    except Exception:
        pass

    from xteink_service.pages import page_at
    pages_by_hash = _pages_by_hash()

    out = []
    for r in rows:
        info = pages_by_hash.get(r["document"])
        total_pages = info["total_pages"] if info else None
        resolved = aliases.get(r["document"])
        # Link to the screenshot book's page when one exists (same canonical
        # slug) so a book's reading log and screenshots share one URL.
        book_slug = (
            screenshot_by_canon.get(canonical_book_slug(resolved), resolved)
            if resolved else None
        )
        out.append({
            **dict(r),
            "title_resolved": resolved,
            "book_slug": book_slug,
            "percentage_display": round(r["percentage"] * 100, 1),
            "at": datetime.fromtimestamp(r["timestamp"], tz=timezone.utc).isoformat(),
            "total_pages": total_pages,
            "page": page_at(r["percentage"], total_pages) if total_pages else None,
            "page_source": info["source"] if info else None,
            "finishes_book": r["id"] == finishing.get(r["document"]),
        })
    return out


@router.get("/api/reading-stats")
async def reading_stats():
    """Aggregate reading stats for the reading-log header.

    Reading volume is reported as PERCENT read — KOReader syncs percentages, not
    page numbers. Progress within a period = the current position minus the
    position at the period's start, counted only for books synced in that period.

    Articles are counted separately: they are real reading, so their pages land
    in ``pages_read``, but they must not inflate the book counters, and summing
    the percentages of a dozen short articles says nothing useful.
    """
    now = datetime.now()
    today = now.date()
    start_today = datetime(today.year, today.month, today.day)
    ts_today = start_today.timestamp()
    ts_week = (start_today - timedelta(days=today.weekday())).timestamp()   # Monday
    ts_month = datetime(today.year, today.month, 1).timestamp()
    ts_year = datetime(today.year, 1, 1).timestamp()

    with _koreader_conn() as conn:
        rows = conn.execute(
            "SELECT document, percentage, timestamp FROM progress_updates"
        ).fetchall()

    try:
        with _state_conn() as conn:
            kinds = kinds_by_hash(conn)
    except Exception:
        kinds = {}

    by_doc: dict[str, list[tuple[int, float]]] = {}
    for r in rows:
        by_doc.setdefault(r["document"], []).append((r["timestamp"], r["percentage"]))

    pages_by_hash = _pages_by_hash()

    counts = {
        "book": {"started": 0, "finished": 0, "in_progress": 0},
        ARTICLE: {"started": 0, "finished": 0, "in_progress": 0},
    }
    pct_today = pct_week = pct_month = 0.0
    pages_today = pages_week = pages_month = 0.0
    # Completed within each period, per kind.
    done: dict[str, dict[str, set[str]]] = {
        kind: {"today": set(), "week": set(), "month": set(), "year": set()}
        for kind in counts
    }

    for doc, pts in by_doc.items():
        kind = ARTICLE if kinds.get(doc) == ARTICLE else "book"
        max_now = max(pct for _, pct in pts)
        info = pages_by_hash.get(doc)
        total_pages = info["total_pages"] if info else 0
        counts[kind]["started"] += 1
        if max_now >= FINISHED:
            counts[kind]["finished"] += 1
            # "Books read" = books COMPLETED within the period: the first sync
            # that reached the finished threshold.
            completed_at = min(ts for ts, pct in pts if pct >= FINISHED)
            for period, since in (("today", ts_today), ("week", ts_week),
                                  ("month", ts_month), ("year", ts_year)):
                if completed_at >= since:
                    done[kind][period].add(doc)
        else:
            counts[kind]["in_progress"] += 1

        if any(ts >= ts_today for ts, _ in pts):
            before = [pct for ts, pct in pts if ts < ts_today]
            delta = max(0.0, max_now - (max(before) if before else 0.0))
            pages_today += delta * total_pages
            if kind != ARTICLE:
                pct_today += delta
        if any(ts >= ts_week for ts, _ in pts):
            before = [pct for ts, pct in pts if ts < ts_week]
            delta = max(0.0, max_now - (max(before) if before else 0.0))
            pages_week += delta * total_pages
            if kind != ARTICLE:
                pct_week += delta
        if any(ts >= ts_month for ts, _ in pts):
            before = [pct for ts, pct in pts if ts < ts_month]
            delta = max(0.0, max_now - (max(before) if before else 0.0))
            pages_month += delta * total_pages
            if kind != ARTICLE:
                pct_month += delta

    def _completed(kind: str) -> dict:
        return {
            **{period: len(docs) for period, docs in done[kind].items()},
            "all_time": counts[kind]["finished"],
        }

    return {
        "books": counts["book"],
        "articles": counts[ARTICLE],
        "read_pct": {
            "today": round(pct_today * 100, 1),
            "week": round(pct_week * 100, 1),
            "month": round(pct_month * 100, 1),
        },
        "pages_read": {
            "today": round(pages_today),
            "week": round(pages_week),
            "month": round(pages_month),
        },
        "books_read": _completed("book"),
        "articles_read": _completed(ARTICLE),
    }


# ------------------------------------------------------------------ #
# Sync devices                                                         #
# ------------------------------------------------------------------ #

@router.get("/api/devices")
async def list_devices():
    """KOReader sync clients that have reported progress.

    Any device running KOReader's Progress sync plugin shows up here — the X4
    (CrossPoint) and, for example, a Kindle running KOReader. `scannable` marks
    the X4, the only device whose book hashes sync_once can auto-resolve.
    """
    from xteink_service.koreader_sync import _SCANNABLE_DEVICE_IDS, _store
    out = []
    for d in _store.devices():
        last_row = None
        try:
            with _koreader_conn() as conn:
                last_row = conn.execute(
                    "SELECT document, percentage FROM progress_updates "
                    "WHERE device_id = ? ORDER BY id DESC LIMIT 1",
                    (d["device_id"],),
                ).fetchone()
        except Exception:
            pass
        out.append({
            **d,
            "scannable": d["device_id"] in _SCANNABLE_DEVICE_IDS,
            "last_document": last_row["document"] if last_row else None,
            "last_percentage_display": (
                round(last_row["percentage"] * 100, 1) if last_row else None
            ),
        })
    return out


# ------------------------------------------------------------------ #
# Phase 9 — Aliases                                                   #
# ------------------------------------------------------------------ #

@router.get("/api/aliases")
async def list_aliases():
    """All document hash → title mappings.

    `linked` marks rows whose book resolves to the same canonical title as
    another row: those hashes are kept in sync with each other across devices.
    Articles are never linked, so they never carry the mark: see
    ``koreader_sync._linked_documents``.
    """
    with _state_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM document_aliases ORDER BY title"
        )]
        kinds = kinds_by_hash(conn)
    counts: dict[str, int] = {}
    for r in rows:
        r["kind"] = kinds.get(r["hash"], "book")
        r["canonical"] = canonical_book_slug(r["title"], r["kind"])
        if r["kind"] != ARTICLE:
            counts[r["canonical"]] = counts.get(r["canonical"], 0) + 1
    for r in rows:
        r["linked"] = r["kind"] != ARTICLE and counts.get(r["canonical"], 0) > 1
    return rows


@router.get("/api/aliases/unresolved")
async def list_unresolved_aliases():
    """Hashes seen in the KOReader reading log that have no title mapping yet.

    Includes the device that last reported each hash: only the X4's hashes can
    be auto-resolved by sync_once, so the UI needs to know which ones must be
    mapped by hand.
    """
    try:
        mapped: set[str] = set()
        with _state_conn() as conn:
            for row in conn.execute("SELECT hash FROM document_aliases"):
                mapped.add(row["hash"])
        with _koreader_conn() as conn:
            rows = conn.execute(
                "SELECT document, MAX(percentage) as pct, MAX(timestamp) as last_seen "
                "FROM progress_updates GROUP BY document ORDER BY last_seen DESC"
            ).fetchall()
            latest_device = {
                r["document"]: (r["device"] or "", r["device_id"] or "")
                for r in conn.execute(
                    "SELECT document, device, device_id FROM progress_updates "
                    "WHERE id IN (SELECT MAX(id) FROM progress_updates GROUP BY document)"
                )
            }
        return [
            {
                "document": r["document"],
                "percentage_display": round(r["pct"] * 100, 1),
                "last_seen": r["last_seen"],
                "device": latest_device.get(r["document"], ("", ""))[0],
                "device_id": latest_device.get(r["document"], ("", ""))[1],
            }
            for r in rows
            if r["document"] not in mapped
        ]
    except Exception:
        return []


class AliasBody(BaseModel):
    title: str
    filename: str = ""


@router.put("/api/aliases/{doc_hash}")
async def set_alias(doc_hash: str, body: AliasBody):
    """Create or update a hash → title mapping.

    INSERT OR REPLACE rewrites the whole row, so the content kind has to be
    carried over explicitly or renaming an article would turn it into a book.
    """
    with sqlite3.connect(_state_db()) as conn:
        _ensure_alias_kind(conn)
        conn.execute(
            "INSERT OR REPLACE INTO document_aliases "
            "(hash, title, filename, resolved_by, computed_at, kind) "
            "VALUES (?,?,?,'manual',?,"
            "COALESCE((SELECT kind FROM document_aliases WHERE hash = ?), 'book'))",
            (doc_hash, body.title, body.filename,
             datetime.now(timezone.utc).isoformat(), doc_hash),
        )
        # index_clipping denormalises the alias title, so a rename staled every
        # clipping of this document.
        search_index.index_clippings_for_document(conn, doc_hash)
    logger.info("Alias set: %s → %s", doc_hash[:12], body.title)
    return {"hash": doc_hash, "title": body.title}


# ------------------------------------------------------------------ #
# Phase 9 — Vault rebuild                                             #
# ------------------------------------------------------------------ #

@router.post("/api/vault/rebuild")
async def rebuild_vault():
    """
    Rebuild all vault markdown from the state DB.
    Useful after vault corruption or to apply format changes to old notes.
    """
    from xteink_service.vault_writer import VaultWriter
    from datetime import date as DateType

    vault = _vault_path()
    vw = VaultWriter(str(vault))
    rebuilt = 0

    with _state_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM synced_screenshots ORDER BY book_title, sync_date, id"
        ).fetchall()

    for row in rows:
        row = dict(row)
        book_title = row["book_title"] or "Unknown"
        sync_date  = row["sync_date"] or date.today().isoformat()
        embed_path = row["vault_png_path"] or ""
        ocr_text   = row["ocr_corrected"] or row["ocr_text"]

        if not embed_path:
            continue

        try:
            day = DateType.fromisoformat(sync_date)
            vw.append_to_daily_note(
                book_title, day, embed_path, ocr_text,
                kind_for_vault_root(row.get("vault_root")),
            )
            rebuilt += 1
        except Exception:
            pass

    return {"rebuilt_notes": rebuilt}


# ------------------------------------------------------------------ #
# Highlights                                                          #
# ------------------------------------------------------------------ #

class HighlightIn(BaseModel):
    selected_text: str


def _find_text_bboxes(png_path: Path, search_text: str) -> tuple[list[dict], int, int]:
    """Return bboxes for words matching search_text."""
    try:
        import re as _re, pytesseract
        from PIL import Image
        from itertools import groupby
        img = Image.open(png_path)
        img_w, img_h = img.size
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        words = [
            {"word": data["text"][i].strip(), "x": data["left"][i], "y": data["top"][i],
             "w": data["width"][i], "h": data["height"][i]}
            for i in range(len(data["text"]))
            if data["text"][i].strip() and int(data["conf"][i]) > 20
        ]
        def norm(s): return _re.sub(r"[^\w]", "", s).lower()
        sw = [norm(w) for w in search_text.split() if norm(w)]
        if not sw: return [], img_w, img_h
        bboxes: list[dict] = []
        for attempt in (len(sw), min(3, len(sw))):
            if attempt < 1: continue
            tgt = sw[:attempt]
            for i in range(len(words) - attempt + 1):
                if [norm(words[i+j]["word"]) for j in range(attempt)] == tgt:
                    bboxes = [{"x": words[i+j]["x"], "y": words[i+j]["y"],
                               "w": words[i+j]["w"], "h": words[i+j]["h"]} for j in range(attempt)]
                    break
            if bboxes: break
        if bboxes:
            merged = []
            for _, g in groupby(sorted(bboxes, key=lambda b: b["y"]), key=lambda b: b["y"]):
                ln = list(g)
                merged.append({"x": min(b["x"] for b in ln), "y": ln[0]["y"],
                               "w": max(b["x"]+b["w"] for b in ln)-min(b["x"] for b in ln),
                               "h": max(b["h"] for b in ln)})
            bboxes = merged
        return bboxes, img_w, img_h
    except Exception:
        return [], 0, 0


@router.get("/api/highlights")
async def list_all_highlights(limit: int = 100):
    """All highlights across all books with screenshot metadata."""
    try:
        with _state_conn() as conn:
            rows = conn.execute('''
                SELECT h.id, h.screenshot_id, h.selected_text,
                       h.bbox_json, h.img_w, h.img_h, h.created_at,
                       s.book_title, s.sync_date, s.vault_png_path
                FROM highlights h
                JOIN synced_screenshots s ON s.id = h.screenshot_id
                WHERE h.deleted_at IS NULL
                ORDER BY h.id DESC LIMIT ?
            ''', (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


@router.get("/api/books/{slug}/highlights")
async def list_book_highlights(slug: str):
    """Highlights for a book, joined to the screenshot each was taken from."""
    try:
        with _state_conn() as conn:
            titles = _book_titles_for_slug(conn, slug)
            if not titles:
                return []
            ph = ",".join("?" for _ in titles)
            rows = conn.execute(f'''
                SELECT h.id, h.screenshot_id, h.selected_text,
                       h.bbox_json, h.img_w, h.img_h, h.created_at,
                       s.book_title, s.sync_date, s.vault_png_path
                FROM highlights h
                JOIN synced_screenshots s ON s.id = h.screenshot_id
                WHERE s.book_title IN ({ph}) AND h.deleted_at IS NULL
                ORDER BY h.id DESC
            ''', titles).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ------------------------------------------------------------------ #
# Export                                                               #
# ------------------------------------------------------------------ #

@router.get("/api/books/{slug}/export")
async def export_book(
    slug: str,
    format: str = "md",
    include: str = ",".join(export.DEFAULT_INCLUDE),
    group: str = "auto",
):
    """One book's passages as Markdown, JSON, or CSV."""
    if format not in export.FORMATS:
        raise HTTPException(
            status_code=400, detail=f"format must be one of {', '.join(export.FORMATS)}"
        )
    if group not in export.GROUP_MODES:
        raise HTTPException(
            status_code=400, detail=f"group must be one of {', '.join(export.GROUP_MODES)}"
        )
    wanted = [part.strip() for part in include.split(",") if part.strip()]
    # A typo must not silently export less than the caller expected.
    unknown = [part for part in wanted if part not in export.INCLUDES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown include: {', '.join(unknown)}")

    try:
        body, media_type, filename = export.build(_state_db(), slug, wanted, format, group)
    except LookupError:
        raise HTTPException(status_code=404, detail="No such book")

    if format == "json":
        return body
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/search")
async def search(
    q: str = "",
    kinds: str = "",
    book: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    notes_only: bool = False,
):
    """Ranked full-text search over screenshots, notes, highlights and clippings.

    `notes_only` is a deprecated alias for `kinds=note` with an empty query,
    which lists every screenshot carrying a note.
    """
    wanted = [k.strip() for k in kinds.split(",") if k.strip() in search_index.KINDS]
    if notes_only and not wanted:
        wanted = ["note"]
    # An empty query browses the selected kinds; a bare /api/search?q= stays empty.
    match_all = bool(wanted) and not q.strip()
    return search_index.search(
        _state_db(), q, wanted, book, limit, offset, match_all=match_all
    )


@router.post("/api/search/reindex")
async def reindex_search():
    """Re-derive the whole search index from the source tables."""
    return search_index.rebuild(_state_db())


@router.get("/api/screenshots/{screenshot_id}/highlights")
async def list_highlights(screenshot_id: int):
    """Return all highlights for a screenshot."""
    try:
        state = SyncState(_state_db())
        return state.list_highlights(screenshot_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/screenshots/{screenshot_id}/highlights")
async def create_highlight(screenshot_id: int, body: HighlightIn):
    """
    Save a text highlight for a screenshot and write ==text== into the Obsidian
    book note so the passage appears highlighted in Obsidian.
    """
    state = SyncState(_state_db())

    # Verify screenshot exists and get metadata
    shot = state.get_screenshot(screenshot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    # Compute word bounding boxes from the vault PNG
    bbox_json = "[]"
    img_w = img_h = 0
    vault_root = _vault_root_of(shot)
    if shot.get("vault_png_path"):
        png_path = _vault_path() / vault_root / shot["vault_png_path"]
        if png_path.exists():
            bboxes, img_w, img_h = _find_text_bboxes(png_path, body.selected_text)
            import json
            bbox_json = json.dumps(bboxes)

    # Store in DB (with bboxes so the UI can render the SVG overlay)
    highlight = state.add_highlight(
        screenshot_id, body.selected_text,
        bbox_json=bbox_json, img_w=img_w, img_h=img_h,
    )
    logger.info("Highlight created: screenshot %d — %.40s", screenshot_id, body.selected_text)

    VaultWriter(str(_vault_path())).mark_highlight(
        shot.get("book_title", ""), body.selected_text, root=vault_root
    )
    return highlight


def _highlight_label(ref_count: int) -> str:
    return "Deleted 1 highlight" + citation_clause(ref_count)


def _restore_highlight(conn: sqlite3.Connection, highlight_id: int) -> dict | None:
    """Take one highlight out of the trash on an open transaction."""
    cur = conn.execute(
        "UPDATE highlights SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
        (highlight_id,),
    )
    if cur.rowcount == 0:
        return None
    try:
        search_index.index_highlight(conn, highlight_id)
    except sqlite3.OperationalError as exc:
        logger.warning("Search index update skipped: %s", exc)
    row = conn.execute("SELECT * FROM highlights WHERE id = ?", (highlight_id,)).fetchone()
    return dict(row) if row else None


def _remark_vault(conn: sqlite3.Connection, screenshot_id: int, selected_text: str) -> bool:
    shot = conn.execute(
        "SELECT * FROM synced_screenshots WHERE id = ?", (screenshot_id,)
    ).fetchone()
    if shot is None:
        return False
    return VaultWriter(str(_vault_path())).mark_highlight(
        dict(shot).get("book_title", ""), selected_text, root=_vault_root_of(shot)
    )


@router.delete("/api/highlights/{highlight_id}")
async def delete_highlight(highlight_id: int):
    """Move a highlight to the trash and strip its ==…== mark from the vault note."""
    state = SyncState(_state_db())
    row = state.get_highlight(highlight_id)
    if row is None or not state.delete_highlight(highlight_id):
        raise HTTPException(status_code=404, detail="Highlight not found")
    unmarked = VaultWriter(str(_vault_path())).unmark_highlight(
        row.get("book_title", ""), row["selected_text"], root=_vault_root_of(row)
    )
    with _state_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        refs = mark_citations(conn, "highlight", highlight_id)
    token = undo.record(
        "highlight_delete",
        _highlight_label(len(refs)),
        {
            "highlight_id": highlight_id,
            "screenshot_id": row["screenshot_id"],
            "book_title": row.get("book_title", ""),
            "selected_text": row["selected_text"],
            "unmarked_vault": unmarked,
            "writing_refs": refs,
        },
    )
    logger.info("Highlight deleted: %d", highlight_id)
    return {
        "deleted": highlight_id, "soft": True, "unmarked_vault": unmarked,
        "writing_refs_marked": len(refs), "undo_token": token,
    }


@router.post("/api/highlights/{highlight_id}/restore")
async def restore_highlight(highlight_id: int):
    """Bring a trashed highlight back, mark and all."""
    SyncState(_state_db())
    with _state_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _restore_highlight(conn, highlight_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Highlight not in the trash")
        _remark_vault(conn, row["screenshot_id"], row["selected_text"])
        restore_citations(conn, deleted_citation_ids(conn, "highlight", highlight_id))
    return row


@undo.register("highlight_delete")
def _undo_highlight_delete(conn: sqlite3.Connection, payload: dict) -> dict:
    highlight_id = int(payload.get("highlight_id", 0))
    row = _restore_highlight(conn, highlight_id)
    if row is None:
        return {
            "restored": [], "skipped": [highlight_id],
            "writing_refs_restored": 0, "marked_vault": False,
        }
    # Putting the mark back is the half of the undo the user sees in Obsidian.
    marked = _remark_vault(conn, row["screenshot_id"], row["selected_text"])
    return {
        "restored": [highlight_id], "skipped": [],
        "writing_refs_restored": restore_citations(conn, payload.get("writing_refs", [])),
        "marked_vault": marked,
    }


# ------------------------------------------------------------------ #
# Trash and undo                                                       #
# ------------------------------------------------------------------ #

_PURGE_GATE = "Pass confirm=1 to purge permanently"
# A purged clipping keeps its tombstone row: the device resurrects any id it
# still holds, so removing the row makes the deletion look silently undone.
_TOMBSTONE_REASON = "tombstone kept so the device does not resurrect the id"


def _excerpt(text: str, limit: int = 160) -> str:
    """First `limit` characters, trimmed on a word boundary."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut) + "..."


def _trashed_writing(conn: sqlite3.Connection) -> list[dict]:
    """The writing bucket. Empty until the writing log lands; never a 500."""
    try:
        entries = conn.execute(
            "SELECT id, book_slug, entry_date, title, body, deleted_at "
            "FROM writing_entries WHERE deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC, id DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    snapshots: dict[int, dict[int, str]] = {}
    counts: dict[int, int] = {}
    try:
        for entry_id, ref_id, snapshot in conn.execute(
            "SELECT entry_id, id, snapshot FROM writing_refs"
        ):
            snapshots.setdefault(entry_id, {})[ref_id] = snapshot or ""
            counts[entry_id] = counts.get(entry_id, 0) + 1
    except sqlite3.OperationalError:
        pass
    titles = {}
    try:
        titles = {canonical_book_slug(t): t for (t,) in conn.execute(
            "SELECT DISTINCT book_title FROM synced_screenshots "
            "WHERE COALESCE(book_title, '') != ''"
        )}
    except sqlite3.OperationalError:
        pass
    return [{
        "id": row["id"],
        "book_title": titles.get(row["book_slug"] or "", ""),
        "book_slug": row["book_slug"] or "",
        "title": row["title"],
        "entry_date": row["entry_date"],
        "excerpt": _excerpt(
            search_index.expand_anchors(row["body"] or "", snapshots.get(row["id"], {}))
        ),
        "ref_count": counts.get(row["id"], 0),
        "deleted_at": row["deleted_at"],
        "restorable": True,
    } for row in entries]


@router.get("/api/trash")
async def trash():
    """Everything recoverable, in named buckets so new kinds are not a breaking change."""
    from xteink_service import clippings as clippings_mod
    state = SyncState(_state_db())
    highlights = state.list_trashed_highlights()
    clips = clippings_mod.list_trashed_clippings()
    with _state_conn() as conn:
        writing = _trashed_writing(conn)
    return {
        "counts": {
            "highlights": len(highlights),
            "clippings": len(clips),
            "writing": len(writing),
        },
        "highlights": highlights,
        "clippings": clips,
        "writing": writing,
    }


@router.delete("/api/trash/highlights/{highlight_id}")
async def purge_trashed_highlight(highlight_id: int, confirm: int = 0):
    if confirm != 1:
        raise HTTPException(status_code=400, detail=_PURGE_GATE)
    if not SyncState(_state_db()).purge_highlight(highlight_id):
        raise HTTPException(status_code=404, detail="Highlight not in the trash")
    logger.info("Highlight purged: %d", highlight_id)
    return {"purged": highlight_id, "row_retained": False, "undo_token": None}


@router.delete("/api/trash/clippings/{clipping_id}")
async def purge_trashed_clipping(clipping_id: int, confirm: int = 0):
    if confirm != 1:
        raise HTTPException(status_code=400, detail=_PURGE_GATE)
    from xteink_service import clippings as clippings_mod
    if not clippings_mod.purge_clipping(clipping_id):
        raise HTTPException(status_code=404, detail="Clipping not in the trash")
    logger.info("Clipping purged: %d", clipping_id)
    return {
        "purged": clipping_id, "row_retained": True,
        "undo_token": None, "reason": _TOMBSTONE_REASON,
    }


@router.post("/api/trash/empty")
async def empty_trash(confirm: int = 0):
    if confirm != 1:
        raise HTTPException(status_code=400, detail=_PURGE_GATE)
    from xteink_service import clippings as clippings_mod
    purged = {
        "highlights": SyncState(_state_db()).purge_trashed_highlights(),
        "clippings": clippings_mod.purge_trashed_clippings(),
        "writing": 0,
    }
    logger.info("Trash emptied: %s", purged)
    return {"purged": purged, "rows_retained": purged["clippings"], "undo_token": None}


class UndoIn(BaseModel):
    token: str


@router.post("/api/undo")
async def run_undo(body: UndoIn):
    """Reverse one logged action. Calling twice is safe and reverses nothing twice."""
    from xteink_service import clippings  # noqa: F401  (registers clipping_delete)
    try:
        return undo.apply(body.token)
    except undo.UnknownToken:
        raise HTTPException(status_code=404, detail="Unknown undo token")
    except undo.ExpiredToken:
        raise HTTPException(
            status_code=410,
            detail="This undo expired. Restore the item from /trash instead.",
        )
    except undo.UnregisteredKind as exc:
        raise HTTPException(
            status_code=409, detail=f"No reversal is registered for kind '{exc.kind}'"
        )


@router.get("/api/undo/recent")
async def list_recent_undo(limit: int = Query(default=5, ge=1, le=20)):
    """Still-actionable undo entries, so a toast survives a reload."""
    return {"items": undo.recent(limit)}


# ------------------------------------------------------------------ #
# Resurfacing — "on this day" and the review queue                     #
# ------------------------------------------------------------------ #

class ReviewSeenIn(BaseModel):
    rating: str = "good"


@router.get("/api/on-this-day")
async def on_this_day(
    day: str = Query(default="", alias="date"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Passages captured on this calendar day in previous years."""
    try:
        when = datetime.strptime(day, "%Y-%m-%d").date() if day else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    return resurface.on_this_day(_state_db(), when, limit)


@router.get("/api/review")
async def review_queue(
    strategy: str = "spaced",
    limit: int = Query(default=10, ge=1, le=50),
    kinds: str = ",".join(resurface.DEFAULT_REVIEW_KINDS),
    seed: str = "",
):
    """A bounded resurfacing queue, newest-due or shuffled."""
    wanted = [part.strip() for part in kinds.split(",") if part.strip()]
    try:
        return resurface.review(_state_db(), strategy, wanted, limit, seed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/review/{kind}/{ref_id}/seen")
async def review_seen(kind: str, ref_id: int, body: ReviewSeenIn):
    """Record that one item was reviewed and advance its schedule."""
    try:
        return resurface.mark_seen(_state_db(), kind, ref_id, body.rating)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ------------------------------------------------------------------ #
# TBR (To Be Read) list                                               #
# ------------------------------------------------------------------ #

class TbrBookIn(BaseModel):
    title: str
    author: str = ""
    source_url: str = ""
    notes: str = ""


class TbrBookUpdate(BaseModel):
    title: str | None = None
    author: str | None = None
    source_url: str | None = None
    notes: str | None = None
    status: str | None = None   # 'queued' | 'reading' | 'done'
    sort_order: int | None = None


def _ensure_tbr(conn: sqlite3.Connection) -> None:
    """Create tbr_books table if it doesn't exist (migration-safe)."""
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


@router.get("/api/tbr/search")
async def search_books(q: str = ""):
    """
    Search Open Library for books to add to TBR.
    No API key required. Returns up to 10 results.
    """
    if not q.strip():
        return []
    import urllib.request, urllib.parse, json as _json
    url = (
        "https://openlibrary.org/search.json?"
        + urllib.parse.urlencode({
            "q": q,
            "fields": "key,title,author_name,cover_i,first_publish_year",
            "limit": 10,
        })
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "xteink-commonplace/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
        results = []
        for doc in data.get("docs", []):
            cover_id = doc.get("cover_i")
            results.append({
                "title": doc.get("title", ""),
                "author": ", ".join(doc.get("author_name", [])[:2]),
                "year": doc.get("first_publish_year"),
                "cover_url": f"https://covers.openlibrary.org/b/id/{cover_id}-S.jpg" if cover_id else None,
                "ol_key": doc.get("key", ""),
            })
        return results
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Open Library search failed: {exc}")


@router.get("/api/tbr")
async def list_tbr():
    """List all TBR books ordered by status (reading first) then sort_order."""
    try:
        with _state_conn() as conn:
            _ensure_tbr(conn)
            rows = conn.execute("""
                SELECT * FROM tbr_books
                ORDER BY
                    CASE status WHEN 'reading' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                    sort_order, id
            """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


@router.post("/api/tbr")
async def add_tbr(body: TbrBookIn):
    """Add a book to the TBR list."""
    with sqlite3.connect(_state_db()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tbr(conn)
        cur = conn.execute(
            "INSERT INTO tbr_books (title, author, source_url, notes) VALUES (?,?,?,?)",
            (body.title, body.author, body.source_url, body.notes),
        )
        row = conn.execute("SELECT * FROM tbr_books WHERE id = ?", (cur.lastrowid,)).fetchone()
    logger.info("TBR added: %s", body.title)
    return dict(row)


@router.put("/api/tbr/{tbr_id}")
async def update_tbr(tbr_id: int, body: TbrBookUpdate):
    """Update a TBR book (status, notes, reorder, etc.)."""
    fields, values = [], []
    for attr in ("title", "author", "source_url", "notes", "status", "sort_order"):
        val = getattr(body, attr)
        if val is not None:
            fields.append(f"{attr} = ?")
            values.append(val)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    values.append(tbr_id)
    with sqlite3.connect(_state_db()) as conn:
        _ensure_tbr(conn)
        cur = conn.execute(
            f"UPDATE tbr_books SET {', '.join(fields)} WHERE id = ?", values
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="TBR book not found")
    return {"updated": tbr_id}


@router.delete("/api/tbr/{tbr_id}")
async def delete_tbr(tbr_id: int):
    """Remove a TBR book."""
    with sqlite3.connect(_state_db()) as conn:
        _ensure_tbr(conn)
        cur = conn.execute("DELETE FROM tbr_books WHERE id = ?", (tbr_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="TBR book not found")
    return {"deleted": tbr_id}


# ------------------------------------------------------------------ #
# Live log viewer (/logs) + device probe                              #
# ------------------------------------------------------------------ #
# A self-contained diagnostic page that streams recent service logs to the
# browser so you can watch what happens when the X4 enters File Transfer mode:
# device detection -> screenshot archiving -> title/hash resolution -> page
# lookup. Served directly by FastAPI (no SvelteKit build step), and — like the
# rest of the CRUD API — refused on PUBLIC_SYNC_HOST by the koreader_sync
# middleware, so it only reaches the Basic-Auth-protected human host
# (xteink.ghostbird.duckdns.org), never the public kosync host.

import time as _time

_device_cache: dict = {"at": 0.0, "online": None}


async def _probe_device(timeout: float = 2.0, cache_s: float = 3.0):
    """Best-effort check whether the X4 is reachable (i.e. in File Transfer
    mode). Cached briefly so multiple tabs/pollers don't hammer the device."""
    now = _time.monotonic()
    if now - _device_cache["at"] < cache_s and _device_cache["online"] is not None:
        return _device_cache["online"]
    host = os.getenv("DEVICE_HOST", "crosspoint.local")
    online = False
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://{host}/api/status",
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                online = resp.status == 200
    except Exception:
        online = False
    _device_cache["at"] = now
    _device_cache["online"] = online
    return online


@router.get("/api/logs")
async def api_logs(
    after: int = 0,
    level: str | None = None,
    contains: str | None = Query(default=None, max_length=200),
):
    """Recent in-memory log records with ``id > after`` (incremental polling)."""
    handler = logbuffer.get_handler() or logbuffer.install()
    return handler.get(after=after, level=level, contains=contains)


@router.get("/api/device")
async def api_device():
    """Whether the X4 is currently reachable (i.e. in File Transfer mode)."""
    return {
        "host": os.getenv("DEVICE_HOST", "crosspoint.local"),
        "online": await _probe_device(),
    }


@router.get("/logs")
async def logs_page() -> HTMLResponse:
    """Self-contained live log viewer (see _LOGS_PAGE)."""
    logbuffer.get_handler() or logbuffer.install()
    return HTMLResponse(_LOGS_PAGE)


_LOGS_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>xteink · logs</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body { background:#0c0f14; color:#cdd6e4; display:flex; flex-direction:column;
         font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { position:sticky; top:0; z-index:2; display:flex; flex-wrap:wrap; gap:.5rem;
           align-items:center; padding:.55rem .75rem; background:#111722; border-bottom:1px solid #222c3a; }
  header h1 { font-size:14px; margin:0 .4rem 0 0; font-weight:600; color:#e8edf5; }
  header h1 .sub { color:#6b7686; font-weight:400; }
  .badge { display:inline-flex; align-items:center; gap:.4rem; padding:.2rem .55rem;
           border-radius:999px; font-size:12px; background:#1b2432; border:1px solid #2a3547; }
  .dot { width:.6rem; height:.6rem; border-radius:50%; background:#556; }
  .badge.online .dot { background:#3ddc84; box-shadow:0 0 8px #3ddc84; }
  .spacer { flex:1 1 auto; }
  select, input[type=text], button { background:#0f1520; color:#cdd6e4; border:1px solid #2a3547;
           border-radius:6px; padding:.3rem .5rem; font:inherit; }
  input[type=text] { min-width:11rem; }
  button { cursor:pointer; }
  button:hover { border-color:#3a4a63; background:#141c29; }
  label.chk { display:inline-flex; align-items:center; gap:.35rem; color:#9aa6b8; user-select:none; }
  main { flex:1 1 auto; overflow:auto; padding:.4rem .2rem 2rem; }
  .row { display:grid; grid-template-columns:auto 4.5rem auto 1fr; gap:.6rem;
         padding:.03rem .75rem; white-space:pre-wrap; word-break:break-word; }
  .row:hover { background:#0f151f; }
  .t { color:#5f6b7e; }
  .lvl { font-weight:600; }
  .src { color:#7aa2f7; }
  .row.INFO .lvl { color:#8b98ad; }
  .row.DEBUG { color:#6b7686; }
  .row.DEBUG .lvl { color:#556; }
  .row.WARNING .lvl, .row.WARNING .msg { color:#e5b567; }
  .row.ERROR .lvl, .row.ERROR .msg,
  .row.CRITICAL .lvl, .row.CRITICAL .msg { color:#ff6b6b; }
  .empty { color:#5f6b7e; padding:1rem .75rem; }
</style>
</head>
<body>
<header>
  <h1>xteink <span class="sub">· logs</span></h1>
  <span id="dev" class="badge"><span class="dot"></span><span id="devtxt">device …</span></span>
  <span class="spacer"></span>
  <select id="level" title="Minimum level">
    <option value="">All</option>
    <option value="DEBUG">Debug+</option>
    <option value="INFO" selected>Info+</option>
    <option value="WARNING">Warn+</option>
    <option value="ERROR">Error+</option>
  </select>
  <input id="filter" type="text" placeholder="filter text…" autocomplete="off">
  <label class="chk"><input id="follow" type="checkbox" checked> follow</label>
  <button id="pause">Pause</button>
  <button id="clear">Clear</button>
  <button id="save">Save</button>
</header>
<main id="log"><div class="empty" id="empty">Waiting for logs… put the X4 into File Transfer mode to watch a sync.</div></main>
<script>
(function(){
  const logEl=document.getElementById('log'), devEl=document.getElementById('dev'),
        devTxt=document.getElementById('devtxt'), levelSel=document.getElementById('level'),
        filterInp=document.getElementById('filter'), followChk=document.getElementById('follow'),
        pauseBtn=document.getElementById('pause');
  const MAX_ROWS=4000;
  let after=0, paused=false, emptyEl=document.getElementById('empty');

  function nearBottom(){ return logEl.scrollHeight-logEl.scrollTop-logEl.clientHeight < 60; }

  function addRow(e){
    if(emptyEl){ emptyEl.remove(); emptyEl=null; }
    const row=document.createElement('div');
    row.className='row '+(e.level||'');
    const t=document.createElement('span'); t.className='t'; t.textContent=e.time||'';
    const l=document.createElement('span'); l.className='lvl'; l.textContent=e.level||'';
    const s=document.createElement('span'); s.className='src';
    s.textContent=(e.name||'').replace(/^xteink_service\./,'');
    const m=document.createElement('span'); m.className='msg'; m.textContent=e.msg||'';
    row.append(t,l,s,m); logEl.appendChild(row);
    while(logEl.childElementCount>MAX_ROWS) logEl.removeChild(logEl.firstChild);
  }

  async function poll(){
    if(paused) return;
    const p=new URLSearchParams({after:String(after)});
    if(levelSel.value) p.set('level',levelSel.value);
    if(filterInp.value.trim()) p.set('contains',filterInp.value.trim());
    try{
      const r=await fetch('/api/logs?'+p.toString(),{cache:'no-store'});
      if(r.ok){
        const d=await r.json();
        const stick=followChk.checked && nearBottom();
        (d.logs||[]).forEach(addRow);
        after=d.last_id||after;
        if(stick) logEl.scrollTop=logEl.scrollHeight;
      }
    }catch(_){}
  }

  async function pollDevice(){
    try{
      const r=await fetch('/api/device',{cache:'no-store'});
      if(r.ok){
        const d=await r.json(), on=d.online===true;
        devEl.className='badge'+(on?' online':'');
        devTxt.textContent=(d.host||'device')+(on?' · File Transfer':' · offline');
      }
    }catch(_){}
  }

  function reset(){ after=0; logEl.innerHTML=''; emptyEl=null; poll(); }

  levelSel.addEventListener('change',reset);
  let fdeb; filterInp.addEventListener('input',()=>{clearTimeout(fdeb); fdeb=setTimeout(reset,300);});
  pauseBtn.addEventListener('click',()=>{paused=!paused; pauseBtn.textContent=paused?'Resume':'Pause'; if(!paused) poll();});
  document.getElementById('clear').addEventListener('click',()=>{logEl.innerHTML=''; emptyEl=null;});
  document.getElementById('save').addEventListener('click',()=>{
    const text=[...logEl.querySelectorAll('.row')].map(r=>r.textContent.replace(/\s+/g,' ').trim()).join('\n');
    const a=document.createElement('a');
    a.href=URL.createObjectURL(new Blob([text],{type:'text/plain'}));
    a.download='xteink-logs.txt'; a.click(); URL.revokeObjectURL(a.href);
  });

  poll(); pollDevice();
  setInterval(poll,1200);
  setInterval(pollDevice,3000);
})();
</script>
</body>
</html>"""
