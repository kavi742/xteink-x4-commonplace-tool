"""
Tests for the clipping → screenshot link (`screenshot_id` on the UI routes).

Uses the isolated-DB pattern from test_api.py: a real sqlite file under
tmp_path, env vars patched before `app` is imported, tables created by hand.
"""
import itertools
import sqlite3

import pytest
from fastapi.testclient import TestClient


_SEQ = itertools.count(1)

DOC = "d" * 32
OTHER_DOC = "e" * 32

CLIP_TEXT = "Now, because Bigland did not get home until after midnight, the house was dark."

# Raw Tesseract output: line breaks mid-sentence and a hyphenated word break.
OCR_TEXT = (
    "Apple.\n"
    "Now, because Big-\n"
    "land did not get home until after\n"
    "midnight, the house was dark.\n"
)

UNRELATED_OCR = "A wholly different passage concerning tramways and the price of coal."


# ------------------------------------------------------------------ #
# Fixtures / helpers                                                   #
# ------------------------------------------------------------------ #

@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated state DB with the screenshot, alias and clipping tables."""
    state_db = str(tmp_path / "state.db")
    vault = tmp_path / "vault"
    vault.mkdir()

    monkeypatch.setenv("STATE_DB", state_db)
    monkeypatch.setenv("KOREADER_DB", str(tmp_path / "koreader.db"))
    monkeypatch.setenv("VAULT_PATH", str(vault))

    with sqlite3.connect(state_db) as conn:
        conn.execute("""
            CREATE TABLE synced_screenshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_path TEXT NOT NULL, content_hash TEXT NOT NULL,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                book_title TEXT DEFAULT '', sync_date TEXT DEFAULT '',
                ocr_text TEXT, vault_png_path TEXT DEFAULT '',
                ocr_corrected TEXT, user_notes TEXT,
                UNIQUE(device_path, content_hash)
            )
        """)
        conn.execute("""
            CREATE TABLE document_aliases (
                hash TEXT PRIMARY KEY, title TEXT NOT NULL,
                filename TEXT DEFAULT '', resolved_by TEXT DEFAULT 'manual',
                computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    from xteink_service.clippings import init_db
    init_db()
    return state_db


@pytest.fixture
def client(env):
    from xteink_service.koreader_sync import app
    return TestClient(app)


def _seed_screenshot(state_db, book="Pastoral", chapter=4, page=11,
                     ocr="", png=None, corrected=None) -> int:
    n = next(_SEQ)
    device_path = f"/screenshots/{book}/{book}_ch{chapter}_p{page}_20pct_{480000 + n}.bmp"
    with sqlite3.connect(state_db) as conn:
        cur = conn.execute(
            "INSERT INTO synced_screenshots "
            "(device_path, content_hash, book_title, sync_date, ocr_text, "
            " vault_png_path, ocr_corrected) VALUES (?,?,?,?,?,?,?)",
            (device_path, f"hash{n:05d}", book, "2026-07-04", ocr,
             png if png is not None else f"{book}/shot-{n:03d}.png", corrected),
        )
        return cur.lastrowid


def _seed_alias(state_db, doc=DOC, title="Pastoral") -> None:
    with sqlite3.connect(state_db) as conn:
        conn.execute("INSERT INTO document_aliases (hash, title) VALUES (?,?)",
                     (doc, title))


def _seed_clipping(state_db, doc=DOC, clip_id="clip-1", spine=3,
                   start_page=10, end_page=10, text=CLIP_TEXT) -> int:
    n = next(_SEQ)
    with sqlite3.connect(state_db) as conn:
        cur = conn.execute(
            "INSERT INTO clippings "
            "(revision, document, clip_id, spine, start_page, end_page, pages, text) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (n, doc, clip_id, spine, start_page, end_page,
             end_page - start_page + 1, text),
        )
        return cur.lastrowid


def _one(client, url="/api/clippings") -> dict:
    r = client.get(url)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1, body
    return body[0]


# ------------------------------------------------------------------ #
# Matching                                                             #
# ------------------------------------------------------------------ #

def test_position_and_text_match(client, env):
    """ch4/p11 is clipping spine 3 / page 10, and the OCR carries the text."""
    _seed_alias(env)
    sid = _seed_screenshot(env, chapter=4, page=11, ocr=OCR_TEXT)
    _seed_clipping(env)

    assert _one(client)["screenshot_id"] == sid


def test_stitched_group_returns_lowest_id(client, env):
    """Stitched pages share one PNG; only the group's MIN(id) is ever served."""
    _seed_alias(env)
    png = "Pastoral/2026-07-04-01.png"
    first = _seed_screenshot(env, chapter=4, page=11, ocr=OCR_TEXT, png=png)
    _seed_screenshot(env, chapter=4, page=12, ocr=OCR_TEXT, png=png)
    _seed_screenshot(env, chapter=4, page=13, ocr=OCR_TEXT, png=png)
    _seed_clipping(env, start_page=11, end_page=11)

    assert _one(client)["screenshot_id"] == first


def test_text_only_match_survives_page_drift(client, env):
    """A clipping made at another font size disagrees on the page number."""
    _seed_alias(env)
    sid = _seed_screenshot(env, chapter=4, page=40, ocr=OCR_TEXT)
    _seed_clipping(env, start_page=10, end_page=10)

    assert _one(client)["screenshot_id"] == sid


def test_position_only_match_when_unambiguous(client, env):
    """No usable OCR, but exactly one screenshot sits at that position."""
    _seed_alias(env)
    sid = _seed_screenshot(env, chapter=4, page=11, ocr="")
    _seed_clipping(env)

    assert _one(client)["screenshot_id"] == sid


def test_ocr_correction_is_preferred_over_raw_ocr(client, env):
    _seed_alias(env)
    sid = _seed_screenshot(env, chapter=9, page=2, ocr="garbled nonsense",
                           corrected=OCR_TEXT)
    _seed_clipping(env)

    assert _one(client)["screenshot_id"] == sid


# ------------------------------------------------------------------ #
# Refusals — a wrong link is worse than none                           #
# ------------------------------------------------------------------ #

def test_unknown_book_returns_null(client, env):
    """No alias row means no slug, so the clipping cannot be placed at all."""
    _seed_screenshot(env, chapter=4, page=11, ocr=OCR_TEXT)
    _seed_clipping(env)

    assert _one(client)["screenshot_id"] is None


def test_no_text_or_position_match_returns_null(client, env):
    _seed_alias(env)
    _seed_screenshot(env, chapter=9, page=3, ocr=UNRELATED_OCR)
    _seed_clipping(env)

    assert _one(client)["screenshot_id"] is None


def test_ambiguous_position_only_returns_null(client, env):
    """Two separate images cover the page range and neither confirms the text."""
    _seed_alias(env)
    _seed_screenshot(env, chapter=4, page=11, ocr=UNRELATED_OCR)
    _seed_screenshot(env, chapter=4, page=12, ocr=UNRELATED_OCR)
    _seed_clipping(env)

    assert _one(client)["screenshot_id"] is None


def test_other_book_screenshots_are_never_matched(client, env):
    _seed_alias(env)
    _seed_screenshot(env, book="Middlemarch", chapter=4, page=11, ocr=OCR_TEXT)
    _seed_clipping(env)

    assert _one(client)["screenshot_id"] is None


def test_missing_screenshot_table_still_serves_clippings(client, env):
    """The clippings UI must not 500 before anything has been archived."""
    with sqlite3.connect(env) as conn:
        conn.execute("DROP TABLE synced_screenshots")
    _seed_alias(env)
    _seed_clipping(env)

    assert _one(client)["screenshot_id"] is None


# ------------------------------------------------------------------ #
# Routes and bulk behaviour                                            #
# ------------------------------------------------------------------ #

def test_book_route_also_carries_screenshot_id(client, env):
    _seed_alias(env)
    sid = _seed_screenshot(env, chapter=4, page=11, ocr=OCR_TEXT)
    _seed_clipping(env)

    assert _one(client, "/api/books/pastoral/clippings")["screenshot_id"] == sid


def test_bulk_matching_does_not_query_per_clipping(client, env, monkeypatch):
    """500 clippings must not cost 500 round trips."""
    from xteink_service import clippings as clip_module

    _seed_alias(env)
    _seed_alias(env, OTHER_DOC, "Middlemarch")
    matched = _seed_screenshot(env, chapter=4, page=11, ocr=OCR_TEXT)
    for page in range(20, 40):
        _seed_screenshot(env, chapter=7, page=page, ocr=UNRELATED_OCR)
    for i in range(150):
        doc = DOC if i % 2 == 0 else OTHER_DOC
        _seed_clipping(env, doc=doc, clip_id=f"clip-{i}")

    opened = 0
    real_connect = clip_module._connect

    def counting_connect():
        nonlocal opened
        opened += 1
        return real_connect()

    monkeypatch.setattr(clip_module, "_connect", counting_connect)

    r = client.get("/api/clippings", params={"limit": 500})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 150
    assert opened <= 6, f"{opened} connections for 150 clippings"
    assert {c["screenshot_id"] for c in body if c["document"] == DOC} == {matched}
    assert {c["screenshot_id"] for c in body if c["document"] == OTHER_DOC} == {None}
