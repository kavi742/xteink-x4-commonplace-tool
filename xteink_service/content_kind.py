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


def reconcile_kinds(conn: sqlite3.Connection, files: "list[tuple[str, str]]") -> int:
    """Correct stored kinds from device paths, given ``(hash, device path)``.

    The device walk is the only authoritative source. kosync metadata carries a
    bare basename because the firmware strips the directory, so a hash first
    seen through Send Metadata is always recorded as a book; without this an
    article stays misfiled forever, since the resolve path only ever touches
    unresolved hashes.
    """
    updated = 0
    for doc_hash, path in files:
        kind = kind_for_device_path(path)
        try:
            cur = conn.execute(
                "UPDATE document_aliases SET kind = ? WHERE hash = ? AND COALESCE(kind,'') != ?",
                (kind, doc_hash, kind),
            )
        except sqlite3.OperationalError:
            return 0  # pre-migration database
        updated += cur.rowcount
    conn.commit()
    return updated


def record_device_files(conn: sqlite3.Connection, files: "list[tuple[str, str]]") -> int:
    """Remember ``(hash, device path)`` from a File Transfer walk.

    ``reconcile_kinds`` can only fix an alias that already exists. A progress
    push often creates the alias *after* the walk, so the path has to outlive
    the walk for that alias to be classified at birth.
    """
    try:
        conn.executemany(
            "INSERT INTO device_files (hash, path) VALUES (?,?) "
            "ON CONFLICT(hash) DO UPDATE SET path = excluded.path, "
            "seen_at = CURRENT_TIMESTAMP",
            list(files),
        )
    except sqlite3.OperationalError:
        return 0  # pre-migration database
    conn.commit()
    return len(files)


def kind_from_device_files(conn: sqlite3.Connection, doc_hash: str) -> str | None:
    """Kind for a hash seen on a previous walk, or None if it was never seen."""
    try:
        row = conn.execute(
            "SELECT path FROM device_files WHERE hash = ?", (doc_hash,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return kind_for_device_path(row[0]) if row else None
