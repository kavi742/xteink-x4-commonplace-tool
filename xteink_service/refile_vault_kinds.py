"""Move vault files that were filed under the wrong content kind.

A screenshot's vault folder is chosen when it is archived. If the service did
not yet know the book was an article (the alias had not been created, or the
device file list had not been recorded), it landed under ``Books/`` and stayed
there even after the kind was corrected.

This re-files those entries: the per-day PNG plus its JSON sidecar, the book
note, and the ``vault_root`` column. It is a dry run unless --apply is passed,
and it only ever moves files, never deletes them.

    python -m xteink_service.refile_vault_kinds
    python -m xteink_service.refile_vault_kinds --apply
"""
import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

from xteink_service import content_kind
from xteink_service.vault_writer import _sanitize

logger = logging.getLogger(__name__)


def plan(state_db: str, vault: str) -> tuple[list[dict], list[str]]:
    """Screenshots whose stored vault_root disagrees with their current kind."""
    conn = sqlite3.connect(state_db)
    conn.row_factory = sqlite3.Row
    notes: list[str] = []

    try:
        rows = conn.execute(
            "SELECT id, book_title, vault_png_path, COALESCE(vault_root,'Books') root "
            "FROM synced_screenshots WHERE COALESCE(book_title,'') != ''"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return [], [f"Cannot read synced_screenshots: {exc}"]

    moves: list[dict] = []
    seen_notes: set[tuple[str, str]] = set()
    for row in rows:
        kind = content_kind.kind_for_title(conn, row["book_title"])
        want = content_kind.vault_root_for(kind)
        if want == row["root"]:
            continue
        if not row["vault_png_path"]:
            notes.append(f"id={row['id']} {row['book_title']!r} has no vault path; row updated only.")
        moves.append({
            "id": row["id"],
            "title": row["book_title"],
            "from_root": row["root"],
            "to_root": want,
            "png": row["vault_png_path"] or "",
            "kind": kind,
        })
        seen_notes.add((row["book_title"], row["root"] + ">" + want))

    for title, direction in sorted(seen_notes):
        src, dst = direction.split(">")
        slug = _sanitize(title)
        if (Path(vault) / dst / f"{slug}.md").exists():
            notes.append(
                f"{dst}/{slug}.md already exists; the {src} note will be kept "
                "alongside it rather than overwritten."
            )
    return moves, notes


def plan_notes(state_db: str, vault: str) -> list[dict]:
    """Book notes sitting under the wrong root for their alias kind.

    The progress path names its note after the alias title, which differs from
    the screenshot folder name, so those notes are a separate set from the ones
    ``plan`` finds.
    """
    conn = sqlite3.connect(state_db)
    conn.row_factory = sqlite3.Row
    out: list[dict] = []
    root = Path(vault)
    seen: set[str] = set()
    for _hash, title, kind in content_kind.alias_rows(conn):
        slug = _sanitize(title)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        want = content_kind.vault_root_for(kind)
        other = "Books" if want == "Articles" else "Articles"
        if (root / other / f"{slug}.md").exists() and not (root / want / f"{slug}.md").exists():
            out.append({"slug": slug, "from_root": other, "to_root": want, "title": title})
    return out


def unwritable(vault: str, moves: list[dict]) -> list[str]:
    """Directories the move would need and cannot write.

    The service writes the vault as root inside its container, so running this
    on the host half-moves things: shutil.move falls back to copy and then
    fails to unlink, leaving the file in both places. Check first.
    """
    root = Path(vault)
    needed = {root}
    for m in moves:
        needed.add(root / m["from_root"])
        needed.add(root / m["to_root"])
        if m["png"]:
            needed.add((root / m["from_root"] / m["png"]).parent)
    return sorted(
        str(d) for d in needed if d.exists() and not os.access(d, os.W_OK | os.X_OK)
    )


def _move(src: Path, dst: Path, applied: list[str]) -> None:
    if not src.exists() or dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    # rename, never copy: a failed copy would leave the file in both folders.
    os.rename(src, dst)
    applied.append(f"{src.name} -> {dst.parent.name}/{dst.name}")


def apply_moves(state_db: str, vault: str, moves: list[dict],
                note_moves: list[dict] | None = None) -> list[str]:
    applied: list[str] = []
    root = Path(vault)
    conn = sqlite3.connect(state_db)
    for m in moves:
        if m["png"]:
            for suffix in (".png", ".json"):
                src = (root / m["from_root"] / m["png"]).with_suffix(suffix)
                dst = (root / m["to_root"] / m["png"]).with_suffix(suffix)
                _move(src, dst, applied)
        conn.execute(
            "UPDATE synced_screenshots SET vault_root = ? WHERE id = ?",
            (m["to_root"], m["id"]),
        )
    # The note is per book, not per screenshot.
    for title, from_root, to_root in {
        (m["title"], m["from_root"], m["to_root"]) for m in moves
    }:
        slug = _sanitize(title)
        _move(root / from_root / f"{slug}.md", root / to_root / f"{slug}.md", applied)
    for n in note_moves or []:
        _move(
            root / n["from_root"] / f"{n['slug']}.md",
            root / n["to_root"] / f"{n['slug']}.md",
            applied,
        )
    conn.commit()

    # Drop folders the move emptied, so the old root stops listing the article.
    for m in moves:
        if not m["png"]:
            continue
        stale = (root / m["from_root"] / m["png"]).parent
        if stale.is_dir() and not any(stale.iterdir()):
            stale.rmdir()
            applied.append(f"removed empty {m['from_root']}/{stale.name}")
    return applied


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", default=os.getenv("STATE_DB", "state.db"))
    p.add_argument("--vault", default=os.getenv("VAULT_PATH", "vault"))
    p.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not os.path.exists(args.db):
        print(f"No such database: {args.db}", file=sys.stderr)
        return 2

    moves, notes = plan(args.db, args.vault)
    note_moves = plan_notes(args.db, args.vault)

    print(f"Database: {args.db}")
    print(f"Vault:    {args.vault}")
    print(f"Mode:     {'APPLY' if args.apply else 'dry run'}\n")
    print(f"Screenshots to re-file: {len(moves)}")
    for m in sorted(moves, key=lambda x: (x["title"], x["id"])):
        print(f"  {m['from_root']} -> {m['to_root']}  [{m['kind']}]  {m['title'][:40]}  {m['png']}")
    print(f"Notes to re-file: {len(note_moves)}")
    for n in sorted(note_moves, key=lambda x: x["slug"]):
        print(f"  {n['from_root']} -> {n['to_root']}  {n['slug']}.md")
    if notes:
        print("\nNotes:")
        for n in notes:
            print(f"  - {n}")

    if not moves and not note_moves:
        print("\nNothing to do.")
        return 0
    if not args.apply:
        print("\nDry run. Re-run with --apply to move the files.")
        return 0

    blocked = unwritable(args.vault, moves)
    if blocked:
        print("\nCannot write these directories, nothing moved:")
        for d in blocked:
            print(f"  {d}")
        print("\nThe service writes the vault as root inside its container, so run")
        print("this there rather than on the host, for example:")
        print("  docker exec <container> python -m xteink_service.refile_vault_kinds --apply")
        return 3

    applied = apply_moves(args.db, args.vault, moves, note_moves)
    print(f"\nMoved {len(applied)} file(s):")
    for line in applied:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
