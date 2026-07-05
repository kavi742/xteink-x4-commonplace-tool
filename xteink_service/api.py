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
  GET  /api/aliases                    — hash → title table
  PUT  /api/aliases/{hash}             — set/update a book title alias
  POST /api/vault/rebuild              — rebuild all vault markdown from DB

Requires STATE_DB, KOREADER_DB, VAULT_PATH env vars (defaulting to /data/*).
"""
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter()

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
                "SELECT COUNT(*) FROM synced_screenshots"
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
                "SELECT COUNT(*) FROM synced_screenshots WHERE sync_date = ?",
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
    """List all books with screenshot counts."""
    try:
        with _state_conn() as conn:
            rows = conn.execute("""
                SELECT book_title,
                       COUNT(*)        AS screenshot_count,
                       MAX(synced_at)  AS last_synced,
                       MAX(sync_date)  AS last_date
                FROM synced_screenshots
                GROUP BY book_title
                ORDER BY book_title
            """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


@router.get("/api/books/{slug}/screenshots")
async def list_screenshots(slug: str):
    """Screenshots for a book (matched by book_title)."""
    try:
        with _state_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM synced_screenshots WHERE book_title = ? ORDER BY id",
                (slug,),
            ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail=f"Book '{slug}' not found")
        return [dict(r) for r in rows]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail=f"Book '{slug}' not found")


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
            "SELECT vault_png_path FROM synced_screenshots WHERE id = ?",
            (screenshot_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    vault_png_path = row["vault_png_path"]
    if not vault_png_path:
        raise HTTPException(status_code=404, detail="No vault path stored for this screenshot")

    png = _vault_path() / "Books" / vault_png_path
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

    aliases: dict[str, str] = {}
    try:
        with _state_conn() as conn:
            for row in conn.execute("SELECT hash, title FROM document_aliases"):
                aliases[row["hash"]] = row["title"]
    except Exception:
        pass

    return [
        {
            **dict(r),
            "title_resolved": aliases.get(r["document"]),
            "percentage_display": round(r["percentage"] * 100, 1),
            "at": datetime.fromtimestamp(r["timestamp"], tz=timezone.utc).isoformat(),
        }
        for r in rows
    ]


# ------------------------------------------------------------------ #
# Phase 9 — Aliases                                                   #
# ------------------------------------------------------------------ #

@router.get("/api/aliases")
async def list_aliases():
    """All document hash → title mappings."""
    with _state_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM document_aliases ORDER BY title"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/aliases/unresolved")
async def list_unresolved_aliases():
    """Hashes seen in the KOReader reading log that have no title mapping yet."""
    try:
        mapped: set[str] = set()
        with _state_conn() as conn:
            for row in conn.execute("SELECT hash FROM document_aliases"):
                mapped.add(row["hash"])
        with _koreader_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT document, MAX(percentage) as pct, MAX(timestamp) as last_seen "
                "FROM progress_updates GROUP BY document ORDER BY last_seen DESC"
            ).fetchall()
        return [
            {
                "document": r["document"],
                "percentage_display": round(r["pct"] * 100, 1),
                "last_seen": r["last_seen"],
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
    """Create or update a hash → title mapping."""
    with sqlite3.connect(_state_db()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO document_aliases "
            "(hash, title, filename, resolved_by, computed_at) VALUES (?,?,?,'manual',?)",
            (doc_hash, body.title, body.filename,
             datetime.now(timezone.utc).isoformat()),
        )
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
            vw.append_to_daily_note(book_title, day, embed_path, ocr_text)
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
                ORDER BY h.id DESC LIMIT ?
            ''', (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


@router.get("/api/search")
async def search(q: str = "", limit: int = 50, notes_only: bool = False):
    """
    Full-text search across book titles, OCR text, OCR corrections,
    user notes, and highlights. Pass notes_only=1 to show only screenshots
    that have user notes set.
    """
    if notes_only:
        # Return all screenshots with non-empty user_notes
        try:
            with _state_conn() as conn:
                rows = conn.execute("""
                    SELECT s.*,
                        GROUP_CONCAT(h.selected_text, '||') AS highlight_matches
                    FROM synced_screenshots s
                    LEFT JOIN highlights h ON h.screenshot_id = s.id
                    WHERE s.user_notes IS NOT NULL AND s.user_notes != ''
                    GROUP BY s.id
                    ORDER BY s.book_title, s.sync_date, s.id
                    LIMIT ?
                """, (limit,)).fetchall()
        except Exception:
            return []
        results = []
        for row in rows:
            d = dict(row)
            hl_raw = d.pop("highlight_matches", None) or ""
            d["highlight_matches"] = [t for t in hl_raw.split("||") if t] if hl_raw else []
            d["match_fields"] = ["user_notes"]
            d["snippet"] = (d.get("user_notes") or "")[:160]
            results.append(d)
        return results

    if not q.strip():
        return []

    like = f"%{q}%"

    try:
        with _state_conn() as conn:
            rows = conn.execute("""
                SELECT DISTINCT s.*,
                    GROUP_CONCAT(h.selected_text, '||') AS highlight_matches
                FROM synced_screenshots s
                LEFT JOIN highlights h ON h.screenshot_id = s.id
                WHERE
                    s.book_title    LIKE ? OR
                    s.ocr_text      LIKE ? OR
                    s.ocr_corrected LIKE ? OR
                    s.user_notes    LIKE ? OR
                    h.selected_text LIKE ?
                GROUP BY s.id
                ORDER BY s.book_title, s.sync_date, s.id
                LIMIT ?
            """, (like, like, like, like, like, limit)).fetchall()
    except Exception:
        return []

    q_lower = q.lower()
    results = []
    for row in rows:
        d = dict(row)
        hl_raw = d.pop("highlight_matches", None) or ""
        d["highlight_matches"] = [t for t in hl_raw.split("||") if t] if hl_raw else []

        # Determine which fields matched
        match_fields = []
        if q_lower in (d.get("book_title") or "").lower():
            match_fields.append("book_title")
        if q_lower in (d.get("ocr_text") or "").lower():
            match_fields.append("ocr_text")
        if q_lower in (d.get("ocr_corrected") or "").lower():
            match_fields.append("ocr_corrected")
        if q_lower in (d.get("user_notes") or "").lower():
            match_fields.append("user_notes")
        if any(q_lower in h.lower() for h in d["highlight_matches"]):
            match_fields.append("highlights")
        d["match_fields"] = match_fields

        # Extract a short snippet from the best matching text field
        snippet = ""
        for field in ("ocr_corrected", "ocr_text", "user_notes"):
            text = d.get(field) or ""
            idx = text.lower().find(q_lower)
            if idx >= 0:
                start = max(0, idx - 60)
                end = min(len(text), idx + len(q) + 60)
                snippet = ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")
                break
        if not snippet and d["highlight_matches"]:
            snippet = d["highlight_matches"][0]
        d["snippet"] = snippet

        results.append(d)

    return results


@router.get("/api/screenshots/{screenshot_id}/highlights")
async def list_highlights(screenshot_id: int):
    """Return all highlights for a screenshot."""
    try:
        from xteink_service.state import SyncState
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
    from xteink_service.state import SyncState
    state = SyncState(_state_db())

    # Verify screenshot exists and get metadata
    shot = state.get_screenshot(screenshot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    # Compute word bounding boxes from the vault PNG
    bbox_json = "[]"
    img_w = img_h = 0
    if shot.get("vault_png_path"):
        png_path = _vault_path() / "Books" / shot["vault_png_path"]
        if png_path.exists():
            bboxes, img_w, img_h = _find_text_bboxes(png_path, body.selected_text)
            import json
            bbox_json = json.dumps(bboxes)

    # Store in DB (with bboxes so the UI can render the SVG overlay)
    highlight = state.add_highlight(
        screenshot_id, body.selected_text,
        bbox_json=bbox_json, img_w=img_w, img_h=img_h,
    )

    # Write ==text== into the vault book note
    vault = _vault_path()
    if vault.exists() and shot.get("book_title") and shot.get("vault_png_path"):
        try:
            from xteink_service.vault_writer import VaultWriter, _sanitize
            book_slug = _sanitize(shot["book_title"])
            note_path = vault / "Books" / f"{book_slug}.md"
            if note_path.exists():
                content = note_path.read_text()
                # Wrap the selected text with == == (Obsidian highlight syntax)
                # Only replace the first unformatted occurrence to avoid double-marking
                marked = f"=={body.selected_text}=="
                if marked not in content and body.selected_text in content:
                    content = content.replace(body.selected_text, marked, 1)
                    note_path.write_text(content)
        except Exception as exc:
            # Vault write failure is non-fatal — DB record is still saved
            import logging
            logging.getLogger(__name__).warning("Vault highlight write failed: %s", exc)

    return highlight


@router.delete("/api/highlights/{highlight_id}")
async def delete_highlight(highlight_id: int):
    """Remove a highlight. Does not un-mark the Obsidian note."""
    from xteink_service.state import SyncState
    state = SyncState(_state_db())
    if not state.delete_highlight(highlight_id):
        raise HTTPException(status_code=404, detail="Highlight not found")
    return {"deleted": highlight_id}


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
