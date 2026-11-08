"""Export one book's passages as Markdown, JSON, or CSV.

Everything below the collection step is pure: `group_items`, `render_md`,
`render_csv`, and `render_json` take rows and return a value, so an export can
be checked without a TestClient.

Soft-deleted material is never exported. A highlight carries `deleted_at` and a
clipping carries `deleted` plus a `trashed_text` holding the words the device
must no longer see, so both predicates are part of the collection queries
rather than a filter applied afterwards.
"""
import csv
import io
import os
import re
import sqlite3
from datetime import datetime, timezone

from xteink_service.booktitle import canonical_book_slug
from xteink_service.content_kind import ARTICLE, alias_rows
from xteink_service.search_index import expand_anchors

FORMATS = ("md", "json", "csv")
INCLUDES = ("highlights", "clippings", "notes", "writing")
GROUP_MODES = ("auto", "chapter", "date")
DEFAULT_INCLUDE = INCLUDES

MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
}

CSV_HEADER = (
    "kind", "book_title", "group", "date", "chapter", "page_label",
    "text", "note", "source_id", "screenshot_id",
)

WRITING_GROUP = "Writing"
UNCHAPTERED = "Unchaptered"
UNDATED = "Undated"

# A reader who cannot find a quotation in the book has to be told why.
_STATE_SUFFIX = {
    "deleted": " - source deleted",
    "drifted": " - source changed since citation",
}

_ANCHOR_RE = re.compile(r"\(\(ref:(\d+)\)\)")
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _state_db() -> str:
    return os.getenv("STATE_DB", "/data/state/state.db")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------------------ #
# Collection                                                           #
# ------------------------------------------------------------------ #

def _article_slugs(conn: sqlite3.Connection) -> set[str]:
    try:
        return {
            canonical_book_slug(title, ARTICLE)
            for _, title, kind in alias_rows(conn)
            if kind == ARTICLE and title
        }
    except sqlite3.OperationalError:
        return set()


def _book_titles(conn: sqlite3.Connection, target: str) -> list[str]:
    """Screenshot folder names whose canonical slug is `target`, articles aside."""
    articles = _article_slugs(conn)
    try:
        rows = conn.execute("SELECT DISTINCT book_title FROM synced_screenshots").fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        r[0] for r in rows
        if r[0] and canonical_book_slug(r[0]) == target
        and canonical_book_slug(r[0], ARTICLE) not in articles
    ]


def _book_hashes(conn: sqlite3.Connection, target: str) -> list[tuple[str, str]]:
    """``(hash, title)`` for every non-article alias resolving to `target`."""
    try:
        return [
            (h, title) for h, title, kind in alias_rows(conn)
            if kind != ARTICLE and canonical_book_slug(title) == target
        ]
    except sqlite3.OperationalError:
        return []


def _page_label(row: sqlite3.Row) -> str:
    """start_page/end_page are pages within the spine item, not book pages."""
    start, end = row["start_page"] + 1, row["end_page"] + 1
    span = f"ch. page {start}" if start == end else f"ch. pages {start}-{end}"
    return f"{span} of {row['pages']}"


def _passage(kind: str, row_id: int, text: str, date: str, **over) -> dict:
    item = {
        "kind": kind, "id": row_id, "text": text or "", "chapter": "",
        "date": date or "", "page_label": "", "note": None, "screenshot_id": None,
    }
    item.update(over)
    return item


def _collect_highlights(conn: sqlite3.Connection, titles: list[str]) -> list[dict]:
    if not titles:
        return []
    ph = ",".join("?" for _ in titles)
    try:
        rows = conn.execute(
            f"""
            SELECT h.id, h.selected_text, h.screenshot_id, COALESCE(s.sync_date, '') AS day
            FROM highlights h
            JOIN synced_screenshots s ON s.id = h.screenshot_id
            WHERE s.book_title IN ({ph}) AND h.deleted_at IS NULL AND h.selected_text != ''
            ORDER BY h.id
            """,
            titles,
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        _passage("highlight", r["id"], r["selected_text"], r["day"],
                 screenshot_id=r["screenshot_id"])
        for r in rows
    ]


def _collect_notes(conn: sqlite3.Connection, titles: list[str]) -> list[dict]:
    if not titles:
        return []
    ph = ",".join("?" for _ in titles)
    try:
        rows = conn.execute(
            f"""
            SELECT id, user_notes, COALESCE(sync_date, '') AS day
            FROM synced_screenshots
            WHERE book_title IN ({ph}) AND user_notes IS NOT NULL AND user_notes != ''
            ORDER BY id
            """,
            titles,
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        _passage("note", r["id"], r["user_notes"], r["day"], screenshot_id=r["id"])
        for r in rows
    ]


def _collect_clippings(conn: sqlite3.Connection, hashes: list[str]) -> list[dict]:
    if not hashes:
        return []
    ph = ",".join("?" for _ in hashes)
    try:
        rows = conn.execute(
            f"""
            SELECT id, text, chapter, spine, start_page, end_page, pages, start_word,
                   CASE WHEN created_at > 0 THEN date(created_at, 'unixepoch')
                        ELSE substr(COALESCE(synced_at, ''), 1, 10) END AS day
            FROM clippings
            WHERE deleted = 0 AND text != '' AND document IN ({ph})
            ORDER BY spine, start_page, start_word
            """,
            hashes,
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        _passage("clipping", r["id"], r["text"], r["day"],
                 chapter=r["chapter"] or "", page_label=_page_label(r))
        for r in rows
    ]


def _ordered_refs(body: str, refs: list[dict]) -> list[dict]:
    """The entry's refs in order of first appearance; uncited rows are dropped."""
    by_id = {r["id"]: r for r in refs}
    seen: set[int] = set()
    out: list[dict] = []
    for match in _ANCHOR_RE.finditer(body or ""):
        ref_id = int(match.group(1))
        if ref_id in by_id and ref_id not in seen:
            seen.add(ref_id)
            out.append(by_id[ref_id])
    return out


def _collect_writing(conn: sqlite3.Connection, slug: str) -> list[dict]:
    """Writing entries for a book. Empty on any database that predates them."""
    try:
        entries = conn.execute(
            "SELECT id, entry_date, title, body, position_pct FROM writing_entries "
            "WHERE book_slug = ? AND deleted_at IS NULL ORDER BY entry_date, id",
            (slug,),
        ).fetchall()
        ref_rows = conn.execute(
            "SELECT id, entry_id, kind, ref_id, document, snapshot, image_path, "
            "position, source_state FROM writing_refs "
            "WHERE entry_id IN (SELECT id FROM writing_entries "
            "                   WHERE book_slug = ? AND deleted_at IS NULL) "
            "ORDER BY entry_id, id",
            (slug,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    by_entry: dict[int, list[dict]] = {}
    for row in ref_rows:
        ref = dict(row)
        ref["anchor"] = f"((ref:{ref['id']}))"
        by_entry.setdefault(ref["entry_id"], []).append(ref)

    return [
        {
            "kind": "writing",
            "id": e["id"],
            "title": e["title"] or "",
            "body": e["body"] or "",
            "date": e["entry_date"] or "",
            "position_pct": e["position_pct"],
            "refs": _ordered_refs(e["body"] or "", by_entry.get(e["id"], [])),
        }
        for e in entries
    ]


def collect(db_path: str, slug: str, include) -> dict:
    """Every included passage for one book, keyed by kind."""
    wanted = {k for k in include if k in INCLUDES}
    target = canonical_book_slug(slug)
    out = {"book_title": "", "highlights": [], "clippings": [], "notes": [], "writing": []}
    if not target:
        return out

    conn = _connect(db_path)
    try:
        titles = _book_titles(conn, target)
        hashes = _book_hashes(conn, target)
        out["book_title"] = titles[0] if titles else (hashes[0][1] if hashes else "")
        if "highlights" in wanted:
            out["highlights"] = _collect_highlights(conn, titles)
        if "notes" in wanted:
            out["notes"] = _collect_notes(conn, titles)
        if "clippings" in wanted:
            out["clippings"] = _collect_clippings(conn, [h for h, _ in hashes])
        if "writing" in wanted:
            out["writing"] = _collect_writing(conn, target)
    finally:
        conn.close()
    return out


def is_empty(collected: dict) -> bool:
    return not collected["book_title"] and not any(collected[k] for k in INCLUDES)


# ------------------------------------------------------------------ #
# Citation expansion                                                   #
# ------------------------------------------------------------------ #

def _quote(text: str) -> str:
    lines = (text or "").strip().splitlines() or [""]
    return "\n".join(f"> {line}" if line.strip() else ">" for line in lines)


def _attribution(kind: str, page_label: str = "", date: str = "") -> str:
    tail = " - ".join(part for part in (page_label, date) if part)
    return f"`{kind}` {tail}".rstrip()


def _ref_text(ref: dict, fmt: str) -> str:
    snapshot = (ref.get("snapshot") or "").strip()
    if fmt != "md":
        return snapshot
    parts: list[str] = []
    if ref.get("kind") == "screenshot" and ref.get("image_path"):
        parts += [f"![[{ref['image_path']}]]", ""]
    if snapshot:
        parts += [_quote(snapshot), ""]
    line = _attribution(f"cited {ref.get('kind') or 'source'}", ref.get("position") or "")
    parts.append(line + _STATE_SUFFIX.get(ref.get("source_state") or "live", ""))
    return "\n".join(parts)


def expand_entry(entry: dict, refs: list[dict], fmt: str) -> str:
    """One entry's body with every citation anchor replaced for `fmt`."""
    snapshots = {ref["id"]: _ref_text(ref, fmt) for ref in refs}
    return expand_anchors(entry.get("body") or "", snapshots)


# ------------------------------------------------------------------ #
# Grouping                                                            #
# ------------------------------------------------------------------ #

def _group_key(item: dict, mode: str) -> str:
    if mode == "chapter":
        if item["kind"] == "writing":
            return WRITING_GROUP
        return item.get("chapter") or UNCHAPTERED
    return item.get("date") or UNDATED


def _chapter_order(buckets: dict, clippings: list[dict]) -> list[str]:
    """Writing first, then chapters in reading order, then whatever has none."""
    order = [WRITING_GROUP] if WRITING_GROUP in buckets else []
    for clip in clippings:
        label = clip["chapter"]
        if label and label in buckets and label not in order:
            order.append(label)
    if UNCHAPTERED in buckets:
        order.append(UNCHAPTERED)
    return order


def _date_order(buckets: dict) -> list[str]:
    dated = sorted((label for label in buckets if label != UNDATED), reverse=True)
    return dated + ([UNDATED] if UNDATED in buckets else [])


def _attach_notes(highlights: list[dict], notes: list[dict]) -> tuple[list[dict], list[dict]]:
    """A note belongs to one screenshot, so it rides along with that screenshot's
    first exported highlight rather than standing on its own."""
    by_shot = {n["screenshot_id"]: n for n in notes}
    taken: set[int] = set()
    out = []
    for highlight in highlights:
        note = by_shot.get(highlight["screenshot_id"])
        if note is not None and note["id"] not in taken:
            taken.add(note["id"])
            highlight = {**highlight, "note": note["text"]}
        out.append(highlight)
    return out, [n for n in notes if n["id"] not in taken]


def group_items(collected: dict, mode: str = "auto") -> tuple[str, list[dict]]:
    """Resolve `auto` and bucket every included item under its group label."""
    clippings = collected.get("clippings", [])
    if mode == "auto":
        mode = "chapter" if any(c["chapter"] for c in clippings) else "date"

    highlights, notes = _attach_notes(
        collected.get("highlights", []), collected.get("notes", [])
    )
    items = collected.get("writing", []) + highlights + clippings + notes
    buckets: dict[str, list[dict]] = {}
    for item in items:
        buckets.setdefault(_group_key(item, mode), []).append(item)
    for bucket in buckets.values():
        bucket.sort(key=lambda i: i["kind"] != "writing")

    order = _chapter_order(buckets, clippings) if mode == "chapter" else _date_order(buckets)
    return mode, [{"label": label, "items": buckets[label]} for label in order]


# ------------------------------------------------------------------ #
# Renderers                                                            #
# ------------------------------------------------------------------ #

_COUNT_NOUNS = (
    ("highlights", "highlight", "highlights"),
    ("clippings", "clipping", "clippings"),
    ("notes", "note", "notes"),
    ("writing", "writing entry", "writing entries"),
)


def _summary(counts: dict) -> str:
    parts = []
    for key, singular, plural in _COUNT_NOUNS:
        n = counts.get(key, 0)
        parts.append(f"{n} {singular if n == 1 else plural}")
    return ", ".join(parts)


def _pct_label(value) -> str:
    return "" if value is None else f"{round(float(value))}%"


def _md_item(item: dict) -> list[str]:
    if item["kind"] == "writing":
        return [
            f"### {item['title'] or 'Untitled'}", "",
            _attribution("writing", _pct_label(item.get("position_pct")), item["date"]), "",
            expand_entry(item, item.get("refs", []), "md").strip(), "",
        ]
    lines = (
        [f"Note: {item['text'].strip()}"] if item["kind"] == "note"
        else [_quote(item["text"])]
    )
    lines += ["", _attribution(item["kind"], item["page_label"], item["date"])]
    if item.get("note"):
        lines.append(f"Note: {item['note'].strip()}")
    lines.append("")
    return lines


def render_md(payload: dict) -> str:
    out = [
        f"# {payload['book_title'] or payload['book_slug']}", "",
        f"> Exported {payload['generated_at'][:10]}. {_summary(payload['counts'])}.", "",
    ]
    for group in payload["groups"]:
        out += [f"## {group['label']}", ""]
        for item in group["items"]:
            out += _md_item(item)
    return "\n".join(out).rstrip() + "\n"


def _csv_row(book_title: str, label: str, item: dict) -> list:
    if item["kind"] == "writing":
        body = expand_entry(item, item.get("refs", []), "csv").strip()
        text = "\n\n".join(part for part in (item["title"], body) if part)
        return ["writing", book_title, label, item["date"], "",
                _pct_label(item.get("position_pct")), text, "", item["id"], ""]
    return [
        item["kind"], book_title, label, item["date"], item["chapter"],
        item["page_label"], item["text"], item.get("note") or "",
        item["id"], item["screenshot_id"] if item["screenshot_id"] is not None else "",
    ]


def render_csv(payload: dict) -> str:
    """The lossy format. A citation's boundaries, source kind, state, and image
    path are all dropped; `md` and `json` are the ones that keep them."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADER)
    for group in payload["groups"]:
        for item in group["items"]:
            writer.writerow(_csv_row(payload["book_title"], group["label"], item))
    return buf.getvalue()


def _json_item(item: dict) -> dict:
    if item["kind"] != "writing":
        return {k: item[k] for k in
                ("kind", "id", "text", "chapter", "date", "page_label", "note", "screenshot_id")}
    refs = item.get("refs", [])
    return {
        "kind": "writing",
        "id": item["id"],
        "title": item["title"],
        # Both forms: dropping the anchors makes a round trip lossy, dropping
        # the expansion makes every consumer reimplement the substitution.
        "body": item["body"],
        "body_expanded": expand_entry(item, refs, "json"),
        "date": item["date"],
        "position_pct": item.get("position_pct"),
        "refs": [
            {
                "anchor": ref["anchor"], "id": ref["id"], "kind": ref["kind"],
                "ref_id": ref["ref_id"], "document": ref["document"],
                "snapshot": ref["snapshot"], "image_path": ref["image_path"],
                "position": ref["position"], "source_state": ref["source_state"],
            }
            for ref in refs
        ],
    }


def render_json(payload: dict) -> dict:
    return {
        **{k: payload[k] for k in
           ("book_slug", "book_title", "generated_at", "include", "grouped_by", "counts")},
        "groups": [
            {"label": g["label"], "items": [_json_item(i) for i in g["items"]]}
            for g in payload["groups"]
        ],
    }


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

def safe_filename(slug: str, fmt: str) -> str:
    """A download name that cannot escape its own quotes or carry a path."""
    cleaned = _UNSAFE_NAME_RE.sub("-", slug or "").strip("-.")
    return f"{cleaned[:80] or 'export'}.{fmt}"


def build(
    db_path: str,
    slug: str,
    include=DEFAULT_INCLUDE,
    fmt: str = "md",
    group: str = "auto",
) -> tuple:
    """``(body, media_type, filename)``. Raises LookupError for an unknown book."""
    collected = collect(db_path, slug, include)
    if is_empty(collected):
        raise LookupError(slug)
    mode, groups = group_items(collected, group)
    payload = {
        "book_slug": canonical_book_slug(slug),
        "book_title": collected["book_title"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "include": [k for k in INCLUDES if k in set(include)],
        "grouped_by": mode,
        "counts": {k: len(collected[k]) for k in INCLUDES},
        "groups": groups,
    }
    filename = safe_filename(slug, fmt)
    if fmt == "json":
        return render_json(payload), MEDIA_TYPES["json"], filename
    if fmt == "csv":
        return render_csv(payload), MEDIA_TYPES["csv"], filename
    return render_md(payload), MEDIA_TYPES["md"], filename
