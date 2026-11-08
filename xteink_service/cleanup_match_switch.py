"""Clean up after switching the device's KOReader Match Method to Filename.

Switching the match method re-keys every document. Progress survives the change
because Smart Sync queries both digests and the server links hashes whose alias
titles share a canonical slug. Clippings do not: clipping sync sends only the
primary hash, so after the switch the device re-uploads its whole local store
under the new filename hash while the old binary-hash rows stay behind. Both
copies then render on the book page.

This script removes the superseded copies and repairs aliases whose title is a
device-side error string instead of a book name.

Run it AFTER switching the setting and completing one manual sync. It is a
dry run unless --apply is passed, and it takes a backup before writing.

    python -m xteink_service.cleanup_match_switch
    python -m xteink_service.cleanup_match_switch --apply
"""
import argparse
import hashlib
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime

from xteink_service.booktitle import canonical_book_slug

logger = logging.getLogger(__name__)

# Titles the device sends when it cannot parse a book. Never real book names.
_ERROR_TITLE = re.compile(r"^\s*(ERROR\b|Error reading\b)", re.IGNORECASE)

_BOOK_EXT = re.compile(r"\.(epub|pdf|mobi|azw3|txt|xtc|xtch)$", re.IGNORECASE)


def _norm_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _basename(path: str | None) -> str:
    return (path or "").replace("\\", "/").split("/")[-1]


def _is_filename_hash(document: str, filename: str | None) -> bool | None:
    """True/False if provable, None when the alias stores no filename."""
    base = _basename(filename)
    if not base:
        return None
    return hashlib.md5(base.encode("utf-8")).hexdigest() == document


def _aliases(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        r["hash"]: r
        for r in conn.execute(
            "SELECT hash, title, filename, resolved_by FROM document_aliases"
        )
    }


# ------------------------------------------------------------------ #
# Clipping de-duplication                                              #
# ------------------------------------------------------------------ #

def plan_clipping_dedup(conn: sqlite3.Connection) -> tuple[list[dict], list[str]]:
    """Find clipping rows superseded by a filename-hash copy of the same book.

    A row is only ever dropped when its book has a document whose hash is
    provably md5(basename) and that document holds the same clipping, matched
    on the device-assigned clip_id or on identical text.
    """
    aliases = _aliases(conn)
    notes: list[str] = []

    documents = [
        r["document"]
        for r in conn.execute("SELECT DISTINCT document FROM clippings")
    ]
    if not documents:
        return [], ["No clippings in the database."]

    keepers: dict[str, list[str]] = {}
    stale: dict[str, list[str]] = {}
    for doc in documents:
        alias = aliases.get(doc)
        if alias is None:
            notes.append(f"{doc[:10]}... has no alias; skipped (cannot identify the book).")
            continue
        verdict = _is_filename_hash(doc, alias["filename"])
        if verdict is None:
            notes.append(
                f"{doc[:10]}... ({alias['title'][:40]}) alias stores no filename; "
                "skipped, cannot prove which method produced it."
            )
            continue
        slug = canonical_book_slug(alias["title"])
        (keepers if verdict else stale).setdefault(slug, []).append(doc)

    deletions: list[dict] = []
    for slug, stale_docs in sorted(stale.items()):
        keeper_docs = keepers.get(slug)
        if not keeper_docs:
            title = aliases[stale_docs[0]]["title"]
            notes.append(
                f"'{title[:40]}' has only old-method clippings and no filename-hash "
                "copy yet; left alone. Sync the book once after the switch."
            )
            continue

        ph = ",".join("?" for _ in keeper_docs)
        keeper_rows = conn.execute(
            f"SELECT clip_id, text FROM clippings WHERE document IN ({ph})",
            keeper_docs,
        ).fetchall()
        keeper_ids = {r["clip_id"] for r in keeper_rows}
        keeper_texts = {_norm_text(r["text"]) for r in keeper_rows if _norm_text(r["text"])}

        for doc in stale_docs:
            for row in conn.execute(
                "SELECT id, clip_id, text, deleted FROM clippings WHERE document = ?",
                (doc,),
            ):
                if row["clip_id"] in keeper_ids:
                    rule = "clip_id"
                elif _norm_text(row["text"]) and _norm_text(row["text"]) in keeper_texts:
                    rule = "text"
                else:
                    notes.append(
                        f"'{aliases[doc]['title'][:34]}' clip {row['clip_id'][:12]} has no "
                        "counterpart under the new hash; KEPT."
                    )
                    continue
                deletions.append({
                    "id": row["id"],
                    "document": doc,
                    "clip_id": row["clip_id"],
                    "title": aliases[doc]["title"],
                    "rule": rule,
                    "preview": _norm_text(row["text"])[:48],
                })

    return deletions, notes


# ------------------------------------------------------------------ #
# Alias repair                                                         #
# ------------------------------------------------------------------ #

def plan_alias_fixes(conn: sqlite3.Connection) -> list[dict]:
    """Aliases whose title is a device error string but whose filename is usable.

    The filename stem is the Calibre-style "Title - Author" form the rest of the
    service already canonicalises, so restoring it is what re-links the row.
    """
    fixes: list[dict] = []
    for row in conn.execute(
        "SELECT hash, title, filename, resolved_by FROM document_aliases"
    ):
        if not _ERROR_TITLE.match(row["title"] or ""):
            continue
        stem = _BOOK_EXT.sub("", _basename(row["filename"])).strip()
        if not stem:
            continue
        fixes.append({
            "hash": row["hash"],
            "old_title": row["title"],
            "new_title": stem,
            "old_slug": canonical_book_slug(row["title"]),
            "new_slug": canonical_book_slug(stem),
        })
    return fixes


# ------------------------------------------------------------------ #
# Apply                                                                #
# ------------------------------------------------------------------ #

def _backup(db_path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = f"{db_path}.bak-{stamp}"
    with sqlite3.connect(db_path) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)
    return dest


def apply_changes(conn: sqlite3.Connection, deletions: list[dict], fixes: list[dict]) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        if deletions:
            conn.executemany(
                "DELETE FROM clippings WHERE id = ?",
                [(d["id"],) for d in deletions],
            )
        for f in fixes:
            # UPDATE rather than INSERT OR REPLACE so later columns (kind) survive.
            conn.execute(
                "UPDATE document_aliases SET title = ?, resolved_by = 'manual' WHERE hash = ?",
                (f["new_title"], f["hash"]),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", default=os.getenv("STATE_DB", "state.db"))
    p.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    p.add_argument("--skip-clippings", action="store_true")
    p.add_argument("--skip-aliases", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not os.path.exists(args.db):
        print(f"No such database: {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    deletions: list[dict] = []
    notes: list[str] = []
    fixes: list[dict] = []

    if not args.skip_clippings:
        deletions, notes = plan_clipping_dedup(conn)
    if not args.skip_aliases:
        fixes = plan_alias_fixes(conn)

    print(f"Database: {args.db}")
    print(f"Mode:     {'APPLY' if args.apply else 'dry run'}\n")

    print(f"Superseded clipping rows to delete: {len(deletions)}")
    for d in sorted(deletions, key=lambda x: (x["title"], x["clip_id"])):
        print(f"  [{d['rule']:7}] {d['title'][:32]:32} {d['clip_id'][:12]}  {d['preview']}")

    if notes:
        print("\nNotes:")
        for n in notes:
            print(f"  - {n}")

    print(f"\nAliases to repair: {len(fixes)}")
    for f in fixes:
        link = "links" if f["new_slug"] else "STILL UNLINKED"
        print(f"  {f['hash'][:10]}... {f['old_title'][:34]!r}")
        print(f"      -> {f['new_title']!r}  (slug {f['new_slug']!r}, {link})")

    if not deletions and not fixes:
        print("\nNothing to do.")
        return 0

    if not args.apply:
        print("\nDry run. Re-run with --apply to commit.")
        return 0

    backup = _backup(args.db)
    print(f"\nBackup written to {backup}")
    apply_changes(conn, deletions, fixes)
    print(f"Deleted {len(deletions)} clipping rows, repaired {len(fixes)} aliases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
