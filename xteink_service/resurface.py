"""On this day, and the review queue.

Both features resurface passages the archive already dates, so they share one
set of candidate queries and one row-to-item projection. Nothing here writes to
the passage tables; `review_log` is the only table this module owns.

A soft-deleted passage is never a candidate. `highlights` carries `deleted_at`
and `clippings` carries `deleted`, and both predicates sit inside the candidate
SQL so no caller can forget them.
"""
import logging
import os
import random
import sqlite3
from datetime import date, timedelta

from xteink_service.booktitle import canonical_book_slug

logger = logging.getLogger(__name__)

KINDS = ("screenshot", "highlight", "clipping")
# An OCR page is a worse review unit than a passage the reader chose.
DEFAULT_REVIEW_KINDS = ("highlight", "clipping")
STRATEGIES = ("spaced", "random")
RATINGS = ("good", "again")

INTERVAL_CAP_DAYS = 365
_EXCERPT_CHARS = 240

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS review_log (
        kind          TEXT    NOT NULL,
        ref_id        INTEGER NOT NULL,
        last_shown    TIMESTAMP,
        shown_count   INTEGER NOT NULL DEFAULT 0,
        interval_days INTEGER NOT NULL DEFAULT 1,
        next_due      TEXT    NOT NULL DEFAULT '',
        last_rating   TEXT    NOT NULL DEFAULT '',
        PRIMARY KEY (kind, ref_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_review_due ON review_log (next_due)",
)


def _state_db() -> str:
    return os.getenv("STATE_DB", "/data/state/state.db")


def init_db(conn: sqlite3.Connection) -> None:
    """Create `review_log` and its index. Idempotent."""
    for statement in _DDL:
        conn.execute(statement)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.commit()
    return conn


# ------------------------------------------------------------------ #
# Candidate sources                                                    #
# ------------------------------------------------------------------ #

# `day` is the date the passage is filed under and the one "on this day"
# matches; `captured_at` is the sort key for "oldest material first". The
# review_log join is from the source table outward, so a stale row left behind
# by a purge can never put a textless item in the queue.
_SOURCES = {
    "screenshot": """
        SELECT 'screenshot' AS kind, s.id AS ref_id, s.id AS screenshot_id,
               COALESCE(s.book_title, '') AS book_title,
               COALESCE(s.sync_date, '')  AS day,
               COALESCE(NULLIF(s.ocr_corrected, ''), s.ocr_text, '') AS body,
               COALESCE(s.sync_date, '')  AS captured_at,
               r.shown_count, r.last_shown, r.next_due
        FROM synced_screenshots s
        LEFT JOIN review_log r ON r.kind = 'screenshot' AND r.ref_id = s.id
        WHERE COALESCE(NULLIF(s.ocr_corrected, ''), s.ocr_text, '') != ''
          AND ({where})
    """,
    "highlight": """
        SELECT 'highlight' AS kind, h.id AS ref_id, h.screenshot_id AS screenshot_id,
               COALESCE(s.book_title, '')   AS book_title,
               substr(COALESCE(h.created_at, ''), 1, 10) AS day,
               h.selected_text              AS body,
               COALESCE(h.created_at, '')   AS captured_at,
               r.shown_count, r.last_shown, r.next_due
        FROM highlights h
        JOIN synced_screenshots s ON s.id = h.screenshot_id
        LEFT JOIN review_log r ON r.kind = 'highlight' AND r.ref_id = h.id
        WHERE h.deleted_at IS NULL AND h.selected_text != ''
          AND ({where})
    """,
    "clipping": """
        SELECT 'clipping' AS kind, c.id AS ref_id, NULL AS screenshot_id,
               COALESCE(a.title, '') AS book_title,
               date(c.created_at, 'unixepoch') AS day,
               c.text                AS body,
               date(c.created_at, 'unixepoch') AS captured_at,
               r.shown_count, r.last_shown, r.next_due
        FROM clippings c
        LEFT JOIN document_aliases a ON a.hash = c.document
        LEFT JOIN review_log r ON r.kind = 'clipping' AND r.ref_id = c.id
        WHERE c.deleted = 0 AND c.text != '' AND c.created_at > 0
          AND ({where})
    """,
}

# The device writes 0 when its clock was never set, which would pile the whole
# archive onto 1 January 1970, so those rows are excluded above.
_ON_THIS_DAY_WHERE = {
    "screenshot": "substr(s.sync_date, 6, 5) = :mmdd AND substr(s.sync_date, 1, 4) < :year",
    "highlight": "substr(h.created_at, 6, 5) = :mmdd AND substr(h.created_at, 1, 4) < :year",
    "clipping": (
        "strftime('%m-%d', c.created_at, 'unixepoch') = :mmdd "
        "AND strftime('%Y', c.created_at, 'unixepoch') < :year"
    ),
}

_SPACED_WHERE = "r.ref_id IS NULL OR r.next_due <= :today"
# Weighting: never-shown first, then earliest next_due, then least recently shown, then oldest.
_SPACED_ORDER = (
    "(next_due IS NOT NULL), COALESCE(next_due, ''), COALESCE(last_shown, ''), "
    "captured_at, ref_id"
)


def _core(kind: str, where: str) -> str:
    return _SOURCES[kind].format(where=where)


def _excerpt(text: str, limit: int = _EXCERPT_CHARS) -> str:
    """Collapse whitespace and trim on a word boundary."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip() + "..."


def _item(row: sqlite3.Row, scheduling: bool, limit: int | None = None) -> dict:
    item = {
        "kind": row["kind"],
        "ref_id": row["ref_id"],
        "screenshot_id": row["screenshot_id"],
        "book_title": row["book_title"],
        "book_slug": canonical_book_slug(row["book_title"]),
        "date": row["day"] or "",
        "text": _excerpt(row["body"], limit) if limit else (row["body"] or ""),
    }
    if scheduling:
        item["shown_count"] = row["shown_count"] or 0
        item["last_shown"] = row["last_shown"]
        item["next_due"] = row["next_due"] or ""
    return item


def _rows(conn: sqlite3.Connection, sql: str, params: dict) -> list[sqlite3.Row]:
    """Run one candidate query, treating a missing source table as no rows."""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        logger.info("Resurface skipped a source: %s", exc)
        return []


# ------------------------------------------------------------------ #
# On this day                                                          #
# ------------------------------------------------------------------ #

def on_this_day(db_path: str, day: date, limit: int = 50) -> dict:
    """Passages captured on this calendar day in previous years, newest year first."""
    params = {"mmdd": day.strftime("%m-%d"), "year": day.strftime("%Y"), "limit": limit}
    conn = _connect(db_path)
    try:
        rows: list[sqlite3.Row] = []
        for kind in KINDS:
            core = _core(kind, _ON_THIS_DAY_WHERE[kind])
            rows += _rows(conn, f"{core} ORDER BY day DESC, ref_id DESC LIMIT :limit", params)
    finally:
        conn.close()

    buckets: dict[int, list[dict]] = {}
    for row in rows:
        year = (row["day"] or "")[:4]
        if year.isdigit():
            buckets.setdefault(int(year), []).append(_item(row, False, _EXCERPT_CHARS))

    years, total = [], 0
    for year in sorted(buckets, reverse=True):
        if total >= limit:
            break
        items = buckets[year][: limit - total]
        total += len(items)
        years.append({"year": year, "years_ago": day.year - year, "items": items})
    return {"date": day.isoformat(), "total": total, "years": years}


# ------------------------------------------------------------------ #
# Review queue                                                         #
# ------------------------------------------------------------------ #

def _count(conn: sqlite3.Connection, kinds: list[str], where: str, params: dict) -> int:
    total = 0
    for kind in kinds:
        rows = _rows(conn, f"SELECT COUNT(*) AS n FROM ({_core(kind, where)})", params)
        total += rows[0]["n"] if rows else 0
    return total


def _spaced(
    conn: sqlite3.Connection, kinds: list[str], limit: int, today: str
) -> list[sqlite3.Row]:
    params = {"today": today, "limit": limit}
    rows: list[sqlite3.Row] = []
    for kind in kinds:
        sql = f"{_core(kind, _SPACED_WHERE)} ORDER BY {_SPACED_ORDER} LIMIT :limit"
        rows += _rows(conn, sql, params)
    rows.sort(key=lambda r: (
        r["next_due"] is not None, r["next_due"] or "", r["last_shown"] or "",
        r["captured_at"] or "", r["ref_id"],
    ))
    return rows[:limit]


def _random(
    conn: sqlite3.Connection, kinds: list[str], limit: int, seed: str
) -> list[sqlite3.Row]:
    # Only the id pairs are loaded, so the ceiling is the archive size rather
    # than the page. A review_log-backed cursor is the upgrade path.
    pairs: list[tuple[str, int]] = []
    for kind in kinds:
        sql = f"SELECT kind, ref_id FROM ({_core(kind, '1 = 1')}) ORDER BY ref_id"
        pairs += [(r["kind"], r["ref_id"]) for r in _rows(conn, sql, {})]
    random.Random(seed).shuffle(pairs)
    chosen = pairs[:limit]

    by_kind: dict[str, list[int]] = {}
    for kind, ref_id in chosen:
        by_kind.setdefault(kind, []).append(ref_id)
    found: dict[tuple[str, int], sqlite3.Row] = {}
    for kind, ids in by_kind.items():
        placeholders = ",".join("?" for _ in ids)
        sql = f"SELECT * FROM ({_core(kind, '1 = 1')}) WHERE ref_id IN ({placeholders})"
        try:
            for row in conn.execute(sql, ids):
                found[(kind, row["ref_id"])] = row
        except sqlite3.OperationalError:
            continue
    return [found[pair] for pair in chosen if pair in found]


def review(
    db_path: str,
    strategy: str = "spaced",
    kinds=None,
    limit: int = 10,
    seed: str = "",
    today: str | None = None,
) -> dict:
    """A bounded resurfacing queue. `today` is injectable so tests get a fixed clock."""
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {', '.join(STRATEGIES)}")
    wanted = [k for k in (kinds or DEFAULT_REVIEW_KINDS) if k in KINDS]
    today = today or date.today().isoformat()

    conn = _connect(db_path)
    try:
        if strategy == "random":
            rows = _random(conn, wanted, limit, seed or today)
            total = _count(conn, wanted, "1 = 1", {})
        else:
            rows = _spaced(conn, wanted, limit, today)
            total = _count(conn, wanted, _SPACED_WHERE, {"today": today})
    finally:
        conn.close()

    return {
        "strategy": strategy,
        "limit": limit,
        "kinds": wanted,
        "total_candidates": total,
        "items": [_item(row, True) for row in rows],
    }


def mark_seen(
    db_path: str,
    kind: str,
    ref_id: int,
    rating: str,
    today: str | None = None,
) -> dict:
    """Advance one item's schedule: `good` doubles the interval, `again` resets it."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if rating not in RATINGS:
        raise ValueError(f"rating must be one of {', '.join(RATINGS)}")
    base = date.fromisoformat(today) if today else date.today()

    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT interval_days FROM review_log WHERE kind = ? AND ref_id = ?",
            (kind, ref_id),
        ).fetchone()
        if rating == "again" or row is None:
            interval = 1
        else:
            interval = min(max(int(row["interval_days"]), 1) * 2, INTERVAL_CAP_DAYS)
        next_due = (base + timedelta(days=interval)).isoformat()
        conn.execute(
            """
            INSERT INTO review_log
                   (kind, ref_id, last_shown, shown_count, interval_days, next_due, last_rating)
            VALUES (?, ?, CURRENT_TIMESTAMP, 1, ?, ?, ?)
            ON CONFLICT(kind, ref_id) DO UPDATE SET
                last_shown    = CURRENT_TIMESTAMP,
                shown_count   = shown_count + 1,
                interval_days = excluded.interval_days,
                next_due      = excluded.next_due,
                last_rating   = excluded.last_rating
            """,
            (kind, ref_id, interval, next_due, rating),
        )
        conn.commit()
        shown = conn.execute(
            "SELECT shown_count FROM review_log WHERE kind = ? AND ref_id = ?",
            (kind, ref_id),
        ).fetchone()["shown_count"]
    finally:
        conn.close()

    return {
        "kind": kind, "ref_id": ref_id, "shown_count": shown,
        "interval_days": interval, "next_due": next_due,
    }
