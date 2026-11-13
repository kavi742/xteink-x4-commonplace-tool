"""Fetch and cache real book cover art from Open Library.

A cover is either the real thing or nothing at all: the UI draws no placeholder,
so a book we cannot find art for simply shows its title. Covers come from the
same Open Library search that backs page counts, via the ``cover_i`` edition id.

Downloading is deliberately narrow. Only ``covers.openlibrary.org`` over https
is reachable, the body is capped, and the bytes must decode as a real
JPEG/PNG/WEBP image before anything is written to the vault. Open Library
answers a missing cover with a tiny placeholder pixel instead of a 404, so an
image whose long edge is under ``_MIN_EDGE`` counts as "no cover" rather than
art.

Both outcomes are cached in ``book_covers``. Recording a miss is what keeps a
coverless book from being searched again on every watcher cycle. The accepted
image is re-encoded small and written beside that book's screenshots at
``<vault>/Books/<Title>/cover.jpg``.
"""
import hashlib
import io
import ipaddress
import logging
import os
import socket
import sqlite3
import urllib.parse
from pathlib import Path

import aiohttp
from PIL import Image

from xteink_service.book_pages import _OL_SEARCH, _USER_AGENT, _clean_query
from xteink_service.content_kind import ARTICLE, alias_rows, kind_for_title
from xteink_service.vault_writer import _sanitize

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_NONE = "none"

SOURCE_AUTO = "openlibrary"
SOURCE_MANUAL = "manual"

_COVER_HOST = "covers.openlibrary.org"
_COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
_TIMEOUT = 15

_MAX_BYTES = 2 * 1024 * 1024
_ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")
# Open Library's missing-cover placeholder is a 1x1 pixel; no real jacket scan
# is this small, so anything under this is a miss rather than art.
_MIN_EDGE = 100
_MAX_EDGE = 400
_JPEG_QUALITY = 82

# Open Library gains covers slowly, so a month is long enough to cost one search
# per coverless book per month and short enough to notice art added since.
_RETRY_NONE_DAYS = 30


# ------------------------------------------------------------------ #
# Cache                                                                #
# ------------------------------------------------------------------ #

def _conn(state_db: str) -> sqlite3.Connection:
    """Open the state DB, ensuring the book_covers table exists."""
    conn = sqlite3.connect(state_db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS book_covers (
            title      TEXT PRIMARY KEY,
            cover_path TEXT NOT NULL DEFAULT '',
            version    TEXT NOT NULL DEFAULT '',
            status     TEXT NOT NULL DEFAULT 'none',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source     TEXT NOT NULL DEFAULT 'openlibrary'
        )
    """)
    try:
        conn.execute(
            "ALTER TABLE book_covers ADD COLUMN source TEXT NOT NULL DEFAULT 'openlibrary'"
        )
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    return conn


def init_db(state_db: str) -> None:
    """Create the book_covers table if it is not there yet."""
    _conn(state_db).close()


def get_covers(state_db: str) -> dict[str, dict]:
    """Return ``{title: {"cover_path", "version", "status", "source"}}``."""
    conn = _conn(state_db)
    try:
        return {
            r[0]: {"cover_path": r[1], "version": r[2], "status": r[3], "source": r[4]}
            for r in conn.execute(
                "SELECT title, cover_path, version, status, source FROM book_covers"
            )
        }
    finally:
        conn.close()


def _store(state_db: str, title: str, cover_path: str, version: str, status: str,
           source: str = SOURCE_AUTO) -> dict:
    conn = _conn(state_db)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO book_covers "
            "(title, cover_path, version, status, updated_at, source) "
            "VALUES (?, ?, ?, ?, datetime('now'), ?)",
            (title, cover_path, version, status, source),
        )
        conn.commit()
    finally:
        conn.close()
    return {"title": title, "cover_path": cover_path, "version": version,
            "status": status, "source": source}


# ------------------------------------------------------------------ #
# Open Library lookup                                                  #
# ------------------------------------------------------------------ #

async def _search_cover_id(session: aiohttp.ClientSession, cleaned: str) -> int | None:
    params = {"q": cleaned, "fields": "cover_i", "limit": 1}
    async with session.get(
        _OL_SEARCH, params=params, headers={"User-Agent": _USER_AGENT}
    ) as resp:
        if resp.status != 200:
            return None
        data = await resp.json(content_type=None)
    for doc in (data or {}).get("docs", []):
        cover_id = doc.get("cover_i")
        if cover_id:
            return int(cover_id)
    return None


async def lookup_cover_url(
    title: str, session: aiohttp.ClientSession | None = None
) -> str | None:
    """Cover image URL for the top Open Library match, or None if it has none."""
    cleaned = _clean_query(title)
    if not cleaned:
        return None
    try:
        if session is None:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT)
            ) as own:
                cover_id = await _search_cover_id(own, cleaned)
        else:
            cover_id = await _search_cover_id(session, cleaned)
    except Exception as exc:  # network / parse / value errors are non-fatal
        logger.debug("Open Library cover lookup failed for %r: %s", title, exc)
        return None
    return _COVER_URL.format(cover_id=cover_id) if cover_id else None


# ------------------------------------------------------------------ #
# Download + validation                                                #
# ------------------------------------------------------------------ #

def _allowed_url(url: str) -> bool:
    """True only for https URLs on the single cover host we fetch from."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and (parts.hostname or "").lower() == _COVER_HOST


async def _download_image(session: aiohttp.ClientSession, url: str) -> bytes | None:
    """Fetch at most ``_MAX_BYTES`` from an allowlisted cover URL."""
    if not _allowed_url(url):
        logger.debug("refusing cover URL outside the allowlist: %r", url)
        return None
    async with session.get(url, headers={"User-Agent": _USER_AGENT}) as resp:
        if resp.status != 200:
            return None
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > _MAX_BYTES:
            return None
        raw = bytearray()
        async for chunk in resp.content.iter_chunked(64 * 1024):
            raw.extend(chunk)
            if len(raw) > _MAX_BYTES:
                logger.debug("cover body over %d bytes, dropped: %r", _MAX_BYTES, url)
                return None
    return bytes(raw)


def _encode_cover(raw: bytes) -> bytes | None:
    """Validate real image bytes and re-encode them as a small JPEG."""
    if not raw:
        return None
    try:
        img = Image.open(io.BytesIO(raw))
        fmt = (img.format or "").upper()
        if fmt not in _ALLOWED_FORMATS:
            return None
        if max(img.size) < _MIN_EDGE:
            return None
        img = img.convert("RGB")
        img.thumbnail((_MAX_EDGE, _MAX_EDGE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    except Exception as exc:
        logger.debug("cover bytes are not a usable image: %s", exc)
        return None
    return buf.getvalue()


def _cover_folder(title: str) -> str | None:
    """Vault folder for a book, matching the one its screenshots are written to."""
    folder = _sanitize(title)
    if folder in ("", ".", ".."):
        return None
    return folder


def _write_cover(vault: str, title: str, data: bytes) -> str | None:
    """Write cover.jpg atomically, returning the Books-relative path."""
    folder = _cover_folder(title)
    if folder is None:
        return None
    book_dir = Path(vault) / "Books" / folder
    book_dir.mkdir(parents=True, exist_ok=True)
    final = book_dir / "cover.jpg"
    tmp = book_dir / "cover.jpg.tmp"
    # Syncthing watches this tree, so a peer must never see a partial cover.
    tmp.write_bytes(data)
    os.replace(tmp, final)
    return f"{folder}/cover.jpg"


async def fetch_cover(
    state_db: str, vault: str, title: str, session: aiohttp.ClientSession | None = None
) -> dict:
    """Download, validate and store one book's cover; cache a miss either way."""
    miss = (title, "", "", STATUS_NONE)

    url = await lookup_cover_url(title, session)
    if not url:
        return _store(state_db, *miss)

    try:
        if session is None:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT)
            ) as own:
                raw = await _download_image(own, url)
        else:
            raw = await _download_image(session, url)
    except Exception as exc:
        logger.debug("cover download failed for %r: %s", title, exc)
        return _store(state_db, *miss)

    data = _encode_cover(raw) if raw else None
    if not data:
        return _store(state_db, *miss)

    cover_path = _write_cover(vault, title, data)
    if not cover_path:
        return _store(state_db, *miss)

    version = hashlib.sha256(data).hexdigest()[:12]
    logger.info("Cover for %s: %s (%s)", title, cover_path, version)
    return _store(state_db, title, cover_path, version, STATUS_OK)


# ------------------------------------------------------------------ #
# Hand-set covers                                                      #
# ------------------------------------------------------------------ #

class CoverError(ValueError):
    """A supplied cover URL could not be used, with a reason for the reader."""


def _reject_private_host(hostname: str) -> None:
    """Refuse a hostname that resolves to anything but a public address.

    The reader supplies this URL and the server is what fetches it, so without
    this the web UI is an SSRF gadget pointed at the homelab: link-local
    metadata, the Docker bridge, other containers, the router.
    """
    try:
        infos = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise CoverError(f"cannot resolve {hostname}") from exc
    if not infos:
        raise CoverError(f"cannot resolve {hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            raise CoverError(f"{hostname} resolves to the private address {ip}")


async def _download_supplied(session: aiohttp.ClientSession, url: str) -> bytes:
    """Fetch a reader-supplied cover URL, with the same cap as the auto path."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise CoverError("the cover URL must start with https://")
    _reject_private_host(parts.hostname)

    # No redirects: a 302 to a private address would walk straight past the
    # check above. Pasting the direct image URL is the fix.
    async with session.get(
        url, headers={"User-Agent": _USER_AGENT}, allow_redirects=False
    ) as resp:
        if resp.status in (301, 302, 303, 307, 308):
            raise CoverError("that URL redirects; paste the direct image link")
        if resp.status != 200:
            raise CoverError(f"the server answered HTTP {resp.status}")
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > _MAX_BYTES:
            raise CoverError("that image is larger than 2 MB")
        raw = bytearray()
        async for chunk in resp.content.iter_chunked(64 * 1024):
            raw.extend(chunk)
            if len(raw) > _MAX_BYTES:
                raise CoverError("that image is larger than 2 MB")
    return bytes(raw)


async def set_cover_from_url(
    state_db: str, vault: str, title: str, url: str,
    session: aiohttp.ClientSession | None = None,
) -> dict:
    """Replace a book's cover with the image at ``url``.

    Raises ``CoverError`` with a reader-facing reason rather than caching a
    miss: an explicit correction that fails should say why, not go quiet.
    """
    if session is None:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_TIMEOUT)
        ) as own:
            raw = await _download_supplied(own, url)
    else:
        raw = await _download_supplied(session, url)

    data = _encode_cover(raw)
    if not data:
        raise CoverError("that file is not a usable JPEG, PNG or WEBP image")

    cover_path = _write_cover(vault, title, data)
    if cover_path is None:
        raise CoverError(f"no vault folder can be derived from the title {title!r}")

    version = hashlib.sha256(data).hexdigest()[:12]
    logger.info("Cover set by hand for %s: %s (%s)", title, cover_path, version)
    return _store(state_db, title, cover_path, version, STATUS_OK, SOURCE_MANUAL)


def clear_cover(state_db: str, vault: str, title: str) -> dict:
    """Drop a book's cover and let the automatic lookup have it again."""
    folder = _cover_folder(title)
    if folder:
        existing = Path(vault) / "Books" / folder / "cover.jpg"
        if existing.is_file():
            existing.unlink()
    conn = _conn(state_db)
    try:
        conn.execute("DELETE FROM book_covers WHERE title = ?", (title,))
        conn.commit()
    finally:
        conn.close()
    logger.info("Cover cleared for %s", title)
    return {"title": title, "status": STATUS_NONE, "source": SOURCE_AUTO}


# ------------------------------------------------------------------ #
# Resolution over the library                                          #
# ------------------------------------------------------------------ #

def _candidate_titles(state_db: str, koreader_db: str) -> list[str]:
    """Every non-article book with reading progress or archived screenshots."""
    try:
        kconn = sqlite3.connect(koreader_db)
        synced = {
            r[0] for r in
            kconn.execute("SELECT DISTINCT document FROM progress_updates")
        }
        kconn.close()
    except sqlite3.OperationalError:
        synced = set()

    conn = _conn(state_db)
    try:
        titles: list[str] = []
        seen: set[str] = set()

        def consider(title: str) -> None:
            if title and title not in seen:
                seen.add(title)
                titles.append(title)

        for doc_hash, title, kind in alias_rows(conn):
            if doc_hash in synced and kind != ARTICLE:
                consider(title)

        try:
            rows = conn.execute(
                "SELECT DISTINCT book_title FROM synced_screenshots "
                "WHERE COALESCE(vault_root, 'Books') = 'Books'"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            # vault_root predates articles, so re-check the alias kind as well.
            if row[0] and kind_for_title(conn, row[0]) != ARTICLE:
                consider(row[0])

        return titles
    finally:
        conn.close()


def _resolved_titles(state_db: str) -> set[str]:
    """Titles with a cover, a recent miss, or a hand-set cover.

    A manual cover is always resolved: a --force refresh must never replace a
    cover the reader chose deliberately with whatever Open Library guesses.
    """
    conn = _conn(state_db)
    try:
        return {
            r[0] for r in conn.execute(
                "SELECT title FROM book_covers "
                "WHERE status = ? OR source = ? OR updated_at > datetime('now', ?)",
                (STATUS_OK, SOURCE_MANUAL, f"-{_RETRY_NONE_DAYS} days"),
            )
        }
    finally:
        conn.close()


def _manual_titles(state_db: str) -> set[str]:
    """Titles whose cover was set by hand."""
    conn = _conn(state_db)
    try:
        return {r[0] for r in conn.execute(
            "SELECT title FROM book_covers WHERE source = ?", (SOURCE_MANUAL,)
        )}
    finally:
        conn.close()


def _books_needing_covers(state_db: str, koreader_db: str) -> list[str]:
    """Candidate books with no cover and no recent miss."""
    resolved = _resolved_titles(state_db)
    return [t for t in _candidate_titles(state_db, koreader_db) if t not in resolved]


async def resolve_book_covers(
    state_db: str, koreader_db: str, vault: str, force: bool = False
) -> dict:
    """Fetch covers for read books lacking one.

    Returns ``{"fetched", "missing", "skipped"}``: covers stored, books Open
    Library had no usable art for, and candidates left alone because they are
    already resolved. ``force`` retries every candidate, including recent misses.
    """
    result = {"fetched": 0, "missing": 0, "skipped": 0}
    try:
        candidates = _candidate_titles(state_db, koreader_db)
        # force retries misses, but never a cover the reader set by hand.
        resolved = _manual_titles(state_db) if force else _resolved_titles(state_db)
    except sqlite3.Error as exc:
        logger.debug("cover resolution skipped: %s", exc)
        return result

    todo = [t for t in candidates if t not in resolved]
    result["skipped"] = len(candidates) - len(todo)
    if not todo:
        return result

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=_TIMEOUT)
    ) as session:
        for title in todo:
            try:
                info = await fetch_cover(state_db, vault, title, session=session)
            except Exception as exc:  # one bad cover must never stop the sync
                logger.debug("cover fetch failed for %r: %s", title, exc)
                result["missing"] += 1
                continue
            if info["status"] == STATUS_OK:
                result["fetched"] += 1
            else:
                result["missing"] += 1

    return result
