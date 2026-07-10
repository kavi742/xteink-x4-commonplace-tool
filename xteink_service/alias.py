"""
Manage hash → book title mappings (document_aliases table).

Usage
-----
List all progress hashes and their current alias (or '?' if unresolved):

    uv run python -m xteink_service.alias \\
        --state  test_scripts/vault/state.db \\
        --koreader test_scripts/vault/koreader.db

Add or update a mapping manually:

    uv run python -m xteink_service.alias \\
        --state test_scripts/vault/state.db \\
        c370c1faafe89878f69442274df5f37f "Fifteen Dogs"

Auto-resolve by scanning epub files on the device (device must be reachable):

    uv run python -m xteink_service.alias \\
        --state test_scripts/vault/state.db \\
        --koreader test_scripts/vault/koreader.db \\
        --auto --device crosspoint.local

DB paths default to the env vars STATE_DB and KOREADER_DB (or /data/state/state.db
and /data/state/koreader.db if unset).
"""
import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import datetime, timezone

import aiohttp

from xteink_service.document_id import compute as _compute_hash, min_bytes_needed


def _state_conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS document_aliases (
            hash        TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            filename    TEXT DEFAULT '',
            resolved_by TEXT DEFAULT 'manual',
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def _unresolved_hashes(state_db: str, koreader_db: str) -> set[str]:
    """Return progress hashes that have no alias yet."""
    try:
        kconn = sqlite3.connect(koreader_db)
        all_hashes = {
            r[0] for r in
            kconn.execute("SELECT DISTINCT document FROM progress_updates").fetchall()
        }
    except sqlite3.OperationalError:
        return set()
    sconn = _state_conn(state_db)
    resolved = {
        r[0] for r in
        sconn.execute("SELECT hash FROM document_aliases").fetchall()
    }
    return all_hashes - resolved


def list_hashes(state_db: str, koreader_db: str) -> None:
    """Print every hash seen in progress_updates with its resolved title."""
    try:
        kconn = sqlite3.connect(koreader_db)
        rows = kconn.execute(
            "SELECT DISTINCT document FROM progress_updates ORDER BY document"
        ).fetchall()
    except sqlite3.OperationalError:
        print(f"No progress_updates table in {koreader_db} — no syncs received yet.")
        return

    sconn = _state_conn(state_db)
    aliases = {
        r[0]: r[1]
        for r in sconn.execute("SELECT hash, title FROM document_aliases").fetchall()
    }

    if not rows:
        print("No progress records found.")
        return

    print(f"{'HASH':<36}  {'TITLE'}")
    print("-" * 60)
    for (doc,) in rows:
        title = aliases.get(doc, "?")
        unresolved = "  ← unresolved" if title == "?" else ""
        print(f"{doc:<36}  {title}{unresolved}")


def add_alias(state_db: str, doc_hash: str, title: str, filename: str = "") -> None:
    """Insert or replace a hash → title mapping."""
    conn = _state_conn(state_db)
    conn.execute(
        "INSERT OR REPLACE INTO document_aliases "
        "(hash, title, filename, resolved_by, computed_at) VALUES (?, ?, ?, 'manual', ?)",
        (doc_hash, title, filename, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    print(f"Mapped  {doc_hash}  →  {title}")


# ------------------------------------------------------------------ #
# Auto-resolve                                                         #
# ------------------------------------------------------------------ #

async def _list_device_books(
    session: aiohttp.ClientSession, host: str
) -> list[dict]:
    """Return all epub/pdf/mobi files found anywhere under / on the device."""
    books: list[dict] = []
    seen: set[str] = set()

    async def walk(path: str, depth: int = 0) -> None:
        if depth > 3 or path in seen:
            return
        seen.add(path)
        try:
            async with session.get(
                f"http://{host}/api/files",
                params={"path": path},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return
                items = await resp.json()
        except Exception as exc:
            print(f"  warn: listing {path!r}: {exc}")
            return
        for item in items:
            if item.get("isDirectory"):
                await walk(f"{path}/{item['name']}", depth + 1)
            elif item["name"].lower().endswith((".epub", ".pdf", ".mobi", ".azw3")):
                books.append({
                    "name": item["name"],
                    "path": f"{path}/{item['name']}",
                    "size": item.get("size", 0),
                })

    await walk("")
    return books


async def _download_for_hash(
    session: aiohttp.ClientSession, host: str, path: str, file_size: int
) -> bytes:
    """Stream just enough bytes to compute the CrossPoint partial-MD5 hash."""
    needed = min_bytes_needed(file_size) if file_size > 0 else 1_100_000
    chunks: list[bytes] = []
    received = 0
    async with session.get(
        f"http://{host}/download",
        params={"path": path},
        timeout=aiohttp.ClientTimeout(total=60),
    ) as resp:
        async for chunk in resp.content.iter_chunked(8192):
            chunks.append(chunk)
            received += len(chunk)
            if received >= needed:
                break
    return b"".join(chunks)


def _lua_hash(data: bytes) -> str:
    """KOReader Lua util.getFileHashPart — different step pattern from CrossPoint spec."""
    import hashlib
    m = hashlib.md5()
    pos, step = 0, 4096
    while pos < len(data):
        m.update(data[pos : pos + 1024])
        pos += step
        step *= 4
    return m.hexdigest()


async def _scan_resolve(state_db: str, koreader_db: str, device_host: str) -> None:
    """Fast resolve: md5(filename) only — no downloads needed."""
    import hashlib

    unresolved = _unresolved_hashes(state_db, koreader_db)
    if not unresolved:
        print("All hashes already resolved.")
        return

    print(f"{len(unresolved)} unresolved hash(es)")
    print(f"Scanning filenames on {device_host} (no downloads)...")

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        books = await _list_device_books(session, device_host)

    matched = 0
    for book in books:
        name = book["name"]
        h = hashlib.md5(name.encode()).hexdigest()
        if h in unresolved:
            title = name.rsplit(".", 1)[0]
            sconn = _state_conn(state_db)
            sconn.execute(
                "INSERT OR REPLACE INTO document_aliases "
                "(hash, title, filename, resolved_by, computed_at) VALUES (?,?,?,'auto',?)",
                (h, title, name, datetime.now(timezone.utc).isoformat()),
            )
            sconn.commit()
            unresolved.discard(h)
            print(f"  Resolved: {h[:8]}... → {title}")
            matched += 1

    print(f"\nResolved {matched} hash(es).")
    if unresolved:
        for h in unresolved:
            print(f"  Still unresolved: {h}  (map manually or try --auto)")


async def _preload_all_aliases(state_db: str, device_host: str, show=None) -> None:
    """
    Proactively map md5(filename) → title for EVERY epub on the device.
    Called during File Transfer mode (port 80 open) so that the first
    KOReader sync for any book writes the vault immediately — no lag.
    Skips filenames already in document_aliases. If ``show`` (the async
    callable from status_display.x4_status) is given, reports progress on
    the X4 screen.
    """
    import hashlib

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            books = await _list_device_books(session, device_host)
        except Exception as exc:
            print(f"  warn: preload scan failed: {exc}")
            return

    if not books:
        return

    if show:
        await show(f"Mapping {len(books)} book(s)...")

    sconn = _state_conn(state_db)
    existing = {
        r[0] for r in sconn.execute("SELECT hash FROM document_aliases").fetchall()
    }

    added = 0
    for book in books:
        name = book["name"]
        h = hashlib.md5(name.encode()).hexdigest()
        if h not in existing:
            title = name.rsplit(".", 1)[0]
            sconn.execute(
                "INSERT OR IGNORE INTO document_aliases "
                "(hash, title, filename, resolved_by, computed_at) VALUES (?,?,?,'auto',?)",
                (h, title, name, datetime.now(timezone.utc).isoformat()),
            )
            added += 1
    sconn.commit()
    if added:
        print(f"  Pre-loaded {added} alias(es) from device file listing")


async def _auto_resolve(state_db: str, koreader_db: str, device_host: str) -> None:
    import hashlib

    unresolved = _unresolved_hashes(state_db, koreader_db)
    if not unresolved:
        print("All hashes already resolved.")
        return

    print(f"{len(unresolved)} unresolved hash(es): {', '.join(unresolved)}")
    print(f"Scanning books on {device_host}...")

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        books = await _list_device_books(session, device_host)
        if not books:
            print("No book files found on device (is it reachable?).")
            return

        print(f"Found {len(books)} book file(s). Downloading headers to compute hashes...")
        matched = 0
        for book in books:
            name = book["name"]
            path = book["path"]
            print(f"  {name} ({book['size']:,} bytes) ... ", end="", flush=True)
            try:
                data = await _download_for_hash(
                    session, device_host, path, book["size"]
                )
                candidates = {
                    "filename_md5": hashlib.md5(name.encode()).hexdigest(),
                    "crosspoint":   _compute_hash(data),
                    "lua":          _lua_hash(data),
                    "path_md5":     hashlib.md5(path.encode()).hexdigest(),
                }
                hit = next((algo for algo, h in candidates.items() if h in unresolved), None)
                if hit:
                    matched_hash = candidates[hit]
                    title = name.rsplit(".", 1)[0]
                    sconn = _state_conn(state_db)
                    sconn.execute(
                        "INSERT OR REPLACE INTO document_aliases "
                        "(hash, title, filename, resolved_by, computed_at) VALUES (?,?,?,'auto',?)",
                        (matched_hash, title, name, datetime.now(timezone.utc).isoformat()),
                    )
                    sconn.commit()
                    unresolved.discard(matched_hash)
                    print(f"MATCH ({hit}) → {title}")
                    matched += 1
                else:
                    print(f"no match ({candidates['crosspoint'][:8]}...)")
            except Exception as exc:
                print(f"error: {exc}")

    print()
    print(f"Resolved {matched} hash(es).")
    if unresolved:
        for h in unresolved:
            print(f"  Still unresolved: {h}")
            print(f"    Map manually:  alias.py {h} \"Book Title\"")


# ------------------------------------------------------------------ #
# CLI                                                                  #
# ------------------------------------------------------------------ #

def main() -> None:
    parser = argparse.ArgumentParser(description="Manage KOReader hash→title aliases")
    parser.add_argument("--state",    default=os.getenv("STATE_DB",    "/data/state/state.db"))
    parser.add_argument("--koreader", default=os.getenv("KOREADER_DB", "/data/state/koreader.db"))
    parser.add_argument("--auto",   action="store_true",
                        help="Auto-resolve by scanning epub files on the device (downloads headers)")
    parser.add_argument("--scan",   action="store_true",
                        help="Fast auto-resolve using filename MD5 only — no downloads")
    parser.add_argument("--device", default=os.getenv("DEVICE_HOST", "crosspoint.local"),
                        help="Device hostname/IP for --auto mode")
    parser.add_argument("hash",   nargs="?", help="Document hash to map")
    parser.add_argument("title",  nargs="?", help="Book title")
    parser.add_argument("--filename", default="", help="Optional epub filename")
    args = parser.parse_args()

    if args.auto:
        asyncio.run(_auto_resolve(args.state, args.koreader, args.device))
    elif args.scan:
        asyncio.run(_scan_resolve(args.state, args.koreader, args.device))
    elif args.hash and args.title:
        add_alias(args.state, args.hash, args.title, args.filename)
    elif args.hash:
        parser.error("Provide both hash and title, e.g.:  alias.py <hash> \"Title\"")
    else:
        list_hashes(args.state, args.koreader)


if __name__ == "__main__":
    main()

