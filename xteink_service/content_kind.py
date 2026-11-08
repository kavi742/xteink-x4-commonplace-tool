"""Tell articles apart from books.

The Instapaper plugin writes articles to the device as EPUBs under
``/Instapaper``, and they then reach this service through exactly the same
channels as a book: clippings keyed on ``md5(filename)``, kosync progress,
and ``/screenshots/<Title>/*.bmp``. Nothing in any of those payloads says
"article", so the device path recorded when an alias is created is the only
signal we get. This module is the single place that decides what counts as an
article, and the only place that knows which vault folder each kind lives in.

Articles are deliberately second-class: they collapse into one "Articles"
pseudo-book in the UI, never count as finished books, and never get looked up
in Open Library.
"""
import os
import sqlite3

from xteink_service.booktitle import canonical_book_slug

ARTICLES_SLUG = "articles"
ARTICLES_TITLE = "Articles"

BOOK = "book"
ARTICLE = "article"

_DEFAULT_PREFIXES = "/Instapaper"


def _normalise(prefix: str) -> str:
    cleaned = prefix.strip().replace("\\", "/").rstrip("/").lower()
    if not cleaned.strip("/"):
        return ""
    return cleaned if cleaned.startswith("/") else "/" + cleaned


def article_path_prefixes() -> tuple[str, ...]:
    """Device directories whose EPUBs are articles, from ARTICLE_PATH_PREFIXES.

    Read on every call so a deployment can change it without a code change and
    tests can override it per case.
    """
    raw = os.getenv("ARTICLE_PATH_PREFIXES") or _DEFAULT_PREFIXES
    return tuple(p for p in (_normalise(part) for part in raw.split(",")) if p)


def kind_for_device_path(path: str) -> str:
    """Classify a file by the device directory it sits in."""
    if not path:
        return BOOK
    norm = path.strip().replace("\\", "/")
    if not norm.startswith("/"):
        norm = "/" + norm
    directory = norm.rsplit("/", 1)[0].lower()
    for prefix in article_path_prefixes():
        if directory == prefix or directory.startswith(prefix + "/"):
            return ARTICLE
    return BOOK


def alias_rows(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """``(hash, title, kind)`` for every alias.

    Falls back to ``book`` for every row on a database that predates the
    ``kind`` column, so a read path never has to care whether the migration in
    ``state.py`` has run yet.
    """
    try:
        rows = conn.execute(
            "SELECT hash, title, COALESCE(kind, '') FROM document_aliases"
        ).fetchall()
    except sqlite3.OperationalError:
        try:
            rows = [
                (r[0], r[1], "")
                for r in conn.execute("SELECT hash, title FROM document_aliases")
            ]
        except sqlite3.OperationalError:
            return []
    return [(r[0], r[1] or "", r[2] or BOOK) for r in rows]


def kinds_by_hash(conn: sqlite3.Connection) -> dict[str, str]:
    """Map every document hash to its content kind."""
    return {h: kind for h, _title, kind in alias_rows(conn)}


def kind_for_title(conn: sqlite3.Connection, title: str) -> str:
    """Classify a free-text title (a screenshot folder name, say) by alias.

    Screenshot folders carry no path, so the only way to recognise an article
    is that an alias with the same canonical slug is already marked as one.
    """
    if not title:
        return BOOK
    target = canonical_book_slug(title, ARTICLE)
    if not target:
        return BOOK
    for _hash, alias_title, kind in alias_rows(conn):
        if kind == ARTICLE and canonical_book_slug(alias_title, ARTICLE) == target:
            return ARTICLE
    return BOOK


def vault_root_for(kind: str) -> str:
    """Top-level vault folder for a content kind."""
    return "Articles" if kind == ARTICLE else "Books"


def kind_for_vault_root(root: str | None) -> str:
    """Inverse of ``vault_root_for``, for rows that only recorded the folder."""
    return ARTICLE if (root or "") == "Articles" else BOOK
