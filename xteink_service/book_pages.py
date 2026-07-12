"""Look up (or estimate) a book's total page count and cache it.

Percentage x total_pages gives an approximate current page for the reading log
and per-book stats. Page counts come from two sources, best first:

1. **Open Library** ``number_of_pages_median`` — a real, human-scale page count
   for the top search match (e.g. Fifteen Dogs -> 175, matching the ~171-page
   print edition). No API key required.
2. **Word-count estimate** (``pages`` module) — download the epub and divide its
   word count by a typical page density. Used only when Open Library has no hit.

Counts are cached in the ``book_pages`` table (keyed by the same title used in
``document_aliases``) so each book is looked up only once. Resolution runs during
File Transfer, when the device is reachable for the epub fallback.
"""
import json
import logging
import re
import sqlite3
import urllib.parse
import urllib.request

import aiohttp

from xteink_service import pages as _pages

logger = logging.getLogger(__name__)

_OL_SEARCH = "https://openlibrary.org/search.json"
_USER_AGENT = "xteink-commonplace/1.0"

# Open Library search parses Lucene syntax, so a bare "-" (as in the
# "Title - Author" filename convention) becomes a NOT operator and drops all
# results. Reduce a query to plain words before searching.
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def _clean_query(query: str) -> str:
    return _NON_WORD.sub(" ", query).strip()


def _conn(state_db: str) -> sqlite3.Connection:
    """Open the state DB, ensuring the book_pages table exists."""
    conn = sqlite3.connect(state_db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS book_pages (
            title       TEXT PRIMARY KEY,
            total_pages INTEGER NOT NULL,
            source      TEXT DEFAULT 'openlibrary',
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def lookup_openlibrary_pages(query: str) -> int | None:
    """Median page count for the top Open Library match, or None if unavailable."""
    cleaned = _clean_query(query)
    if not cleaned:
        return None
    url = _OL_SEARCH + "?" + urllib.parse.urlencode({
        "q": cleaned,
        "fields": "title,number_of_pages_median",
        "limit": 1,
    })
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.load(resp)
        for doc in data.get("docs", []):
            pages_n = doc.get("number_of_pages_median")
            if pages_n and int(pages_n) > 0:
                return int(pages_n)
    except Exception as exc:  # network / parse / value errors are non-fatal
        logger.debug("Open Library page lookup failed for %r: %s", query, exc)
    return None


def get_page_counts(state_db: str) -> dict[str, dict]:
    """Return ``{title: {"total_pages": int, "source": str}}`` for cached books."""
    conn = _conn(state_db)
    try:
        return {
            row[0]: {"total_pages": row[1], "source": row[2]}
            for row in conn.execute(
                "SELECT title, total_pages, source FROM book_pages"
            )
        }
    finally:
        conn.close()


def _store(state_db: str, title: str, total_pages: int, source: str) -> None:
    conn = _conn(state_db)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO book_pages (title, total_pages, source, updated_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (title, total_pages, source),
        )
        conn.commit()
    finally:
        conn.close()


def _books_needing_pages(state_db: str, koreader_db: str) -> list[tuple[str, str]]:
    """Books that have reading progress but no cached page count.

    Returns ``[(title, filename), ...]`` for aliased hashes seen in
    progress_updates whose title is not yet in book_pages.
    """
    try:
        kconn = sqlite3.connect(koreader_db)
        synced = {
            r[0] for r in
            kconn.execute("SELECT DISTINCT document FROM progress_updates")
        }
        kconn.close()
    except sqlite3.OperationalError:
        synced = set()
    if not synced:
        return []

    conn = _conn(state_db)
    try:
        have = {r[0] for r in conn.execute("SELECT title FROM book_pages")}
        todo: list[tuple[str, str]] = []
        seen: set[str] = set()
        for h, title, filename in conn.execute(
            "SELECT hash, title, filename FROM document_aliases"
        ):
            if h in synced and title and title not in have and title not in seen:
                todo.append((title, filename or title))
                seen.add(title)
        return todo
    finally:
        conn.close()


async def _estimate_from_device(
    device_host: str, query: str, filename: str, device_books: list[dict]
) -> int | None:
    """Download the matching epub from the device and estimate its pages."""
    match = next(
        (
            b for b in device_books
            if b["name"] == filename or b["name"].rsplit(".", 1)[0] == query
        ),
        None,
    )
    if not match:
        return None
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60)
        ) as session:
            async with session.get(
                f"http://{device_host}/download",
                params={"path": match["path"]},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
        pages_n = _pages.estimate_total_pages(data)
        return pages_n or None
    except Exception as exc:
        logger.debug("epub estimate failed for %r: %s", query, exc)
        return None


async def resolve_book_pages(
    state_db: str, koreader_db: str, device_host: str, show=None
) -> int:
    """Look up + cache page counts for read books lacking them.

    For each book that has KOReader progress but no cached page count, try Open
    Library first, then fall back to a word-count estimate from the epub on the
    device. Returns the number of books newly resolved. If ``show`` (from
    ``status_display.x4_status``) is given, reports progress on the X4 screen.
    """
    todo = _books_needing_pages(state_db, koreader_db)
    if not todo:
        return 0

    if show:
        await show(f"Looking up pages for {len(todo)} book(s)...")

    device_books: list[dict] | None = None  # lazily loaded for the fallback only
    resolved = 0

    for title, filename in todo:
        query = filename.rsplit(".", 1)[0] if filename else title
        total_pages = lookup_openlibrary_pages(query)
        source = "openlibrary"

        if not total_pages:
            if device_books is None:
                from xteink_service.alias import _list_device_books
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as session:
                        device_books = await _list_device_books(session, device_host)
                except Exception as exc:
                    logger.debug("device listing for page estimate failed: %s", exc)
                    device_books = []
            total_pages = await _estimate_from_device(
                device_host, query, filename, device_books
            )
            source = "estimate"

        if total_pages and total_pages > 0:
            _store(state_db, title, total_pages, source)
            logger.info("Pages for %s: %d (%s)", title, total_pages, source)
            resolved += 1

    return resolved
