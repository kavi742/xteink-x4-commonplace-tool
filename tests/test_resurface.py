"""Resurfacing: "on this day" and the review queue.

The contract that matters most is negative: something the user deleted must
never come back round. Everything else is scheduling arithmetic, and it is
pinned to an injected clock so a run in December asserts the same thing as a
run in July.
"""
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from xteink_service import clippings, resurface
from xteink_service import koreader_sync as ks
from xteink_service.koreader_sync import app
from xteink_service.state import SyncState

BOOK = "Pastoral"
DOC = "ffffffffffffffffffffffffffffff01"
TODAY = "2026-11-08"


@pytest.fixture
def env(tmp_path, monkeypatch):
    state_db = str(tmp_path / "state.db")
    vault = tmp_path / "vault"
    (vault / "Books").mkdir(parents=True)
    monkeypatch.setenv("STATE_DB", state_db)
    monkeypatch.setenv("KOREADER_DB", str(tmp_path / "koreader.db"))
    monkeypatch.setenv("VAULT_PATH", str(vault))
    SyncState(state_db)
    clippings.init_db()
    return state_db


@pytest.fixture
def client(env):
    return TestClient(app)


def _exec(db: str, sql: str, params: tuple = ()) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).lastrowid


def _epoch(day: str) -> int:
    return int(datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp())


def _screenshot(db: str, day: str, ocr: str = "OCR text", book: str = BOOK) -> int:
    return _exec(
        db,
        "INSERT INTO synced_screenshots "
        "(device_path, content_hash, book_title, sync_date, ocr_text) VALUES (?,?,?,?,?)",
        (f"/screenshots/{book}/{day}-{ocr[:10]}.bmp", f"h-{day}-{ocr[:10]}", book, day, ocr),
    )


def _highlight(db: str, screenshot_id: int, text: str, day: str = "2025-11-07",
               deleted: str | None = None) -> int:
    return _exec(
        db,
        "INSERT INTO highlights (screenshot_id, selected_text, created_at, deleted_at) "
        "VALUES (?,?,?,?)",
        (screenshot_id, text, f"{day} 10:00:00", deleted),
    )


def _alias(db: str, doc_hash: str = DOC, title: str = BOOK) -> None:
    _exec(db, "INSERT OR IGNORE INTO document_aliases (hash, title) VALUES (?,?)",
          (doc_hash, title))


def _clipping(db: str, clip_id: str, text: str, created: int, deleted: int = 0) -> int:
    _alias(db)
    return _exec(
        db,
        "INSERT INTO clippings (revision, document, clip_id, deleted, text, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (1, DOC, clip_id, deleted, text, created),
    )


def _review_row(db: str, kind: str, ref_id: int, next_due: str,
                interval: int = 4, last_shown: str = "2026-10-24 18:02:11") -> None:
    with sqlite3.connect(db) as conn:
        resurface.init_db(conn)
        conn.execute(
            "INSERT INTO review_log (kind, ref_id, last_shown, shown_count, "
            "interval_days, next_due, last_rating) VALUES (?,?,?,?,?,?,'good')",
            (kind, ref_id, last_shown, 2, interval, next_due),
        )


def _refs(result: dict) -> list[tuple[str, int]]:
    return [(i["kind"], i["ref_id"]) for i in result["items"]]


# ------------------------------------------------------------------ #
# On this day                                                          #
# ------------------------------------------------------------------ #

def test_on_this_day_matches_month_and_day_across_years(env):
    _screenshot(env, "2024-11-07", "Two years back")
    _screenshot(env, "2025-11-07", "One year back")
    _screenshot(env, "2026-11-07", "This very year")
    _screenshot(env, "2025-11-08", "The day after")

    result = resurface.on_this_day(env, date(2026, 11, 7))

    assert [y["year"] for y in result["years"]] == [2025, 2024]
    assert [y["years_ago"] for y in result["years"]] == [1, 2]
    assert result["total"] == 2
    texts = [i["text"] for y in result["years"] for i in y["items"]]
    assert texts == ["One year back", "Two years back"]


def test_on_this_day_empty_returns_zero_not_404(env):
    result = resurface.on_this_day(env, date(2026, 11, 7))
    assert result == {"date": "2026-11-07", "total": 0, "years": []}


def test_on_this_day_covers_all_three_kinds(env):
    shot = _screenshot(env, "2025-11-07", "A page of OCR")
    _highlight(env, shot, "A sentence worth keeping.", day="2025-11-07")
    _clipping(env, "cp-1", "An excerpt.", created=_epoch("2024-11-07"))

    result = resurface.on_this_day(env, date(2026, 11, 7))

    kinds = {i["kind"] for y in result["years"] for i in y["items"]}
    assert kinds == {"screenshot", "highlight", "clipping"}


def test_on_this_day_excludes_zero_epoch_clippings(env):
    _clipping(env, "cp-zero", "The clock was never set.", created=0)

    for day in (date(1970, 1, 1), date(2026, 1, 1), date(2026, 11, 7)):
        assert resurface.on_this_day(env, day)["total"] == 0


def test_on_this_day_truncates_on_a_word_boundary(env):
    _screenshot(env, "2025-11-07", "word " * 200)

    item = resurface.on_this_day(env, date(2026, 11, 7))["years"][0]["items"][0]
    assert len(item["text"]) <= 243
    assert item["text"].endswith("...")
    assert "wor..." not in item["text"]


def test_on_this_day_respects_limit(env):
    for n in range(5):
        _screenshot(env, "2025-11-07", f"Passage {n}")

    result = resurface.on_this_day(env, date(2026, 11, 7), limit=2)
    assert result["total"] == 2
    assert len(result["years"][0]["items"]) == 2


def test_on_this_day_degrades_when_a_table_is_missing(env):
    _screenshot(env, "2025-11-07", "Still readable")
    _clipping(env, "cp-1", "About to vanish.", created=_epoch("2025-11-07"))
    _exec(env, "DROP TABLE clippings")

    result = resurface.on_this_day(env, date(2026, 11, 7))
    assert [i["kind"] for y in result["years"] for i in y["items"]] == ["screenshot"]


def test_on_this_day_bad_date_returns_400(env, client):
    assert client.get("/api/on-this-day?date=7th%20November").status_code == 400
    assert client.get("/api/on-this-day?date=2026-13-45").status_code == 400
    assert client.get("/api/on-this-day").status_code == 200


def test_on_this_day_excludes_soft_deleted_items(env):
    shot = _screenshot(env, "2025-11-07", "A page of OCR")
    _highlight(env, shot, "Still here.", day="2025-11-07")
    _highlight(env, shot, "Deleted.", day="2025-11-07", deleted="2026-11-08 09:00:00")
    _clipping(env, "cp-live", "Live excerpt.", created=_epoch("2025-11-07"))
    _clipping(env, "cp-dead", "", created=_epoch("2025-11-07"), deleted=1)

    texts = {
        i["text"] for y in resurface.on_this_day(env, date(2026, 11, 7))["years"]
        for i in y["items"]
    }
    assert "Deleted." not in texts
    assert {"Still here.", "Live excerpt.", "A page of OCR"} == texts


# ------------------------------------------------------------------ #
# Review queue                                                         #
# ------------------------------------------------------------------ #

def _seed_many(env, n: int = 20) -> int:
    shot = _screenshot(env, "2025-11-07")
    for i in range(n):
        _highlight(env, shot, f"Passage {i}", day="2025-11-07")
    return shot


def test_review_rejects_an_unknown_strategy(env):
    with pytest.raises(ValueError):
        resurface.review(env, "sm2")


def test_review_respects_limit(env):
    _seed_many(env, 20)

    for strategy in resurface.STRATEGIES:
        result = resurface.review(env, strategy, None, 3, seed="s", today=TODAY)
        assert len(result["items"]) == 3
        assert result["total_candidates"] == 20


def test_review_random_is_stable_for_a_seed(env):
    _seed_many(env, 30)

    first = _refs(resurface.review(env, "random", None, 10, seed="2026-11-07"))
    again = _refs(resurface.review(env, "random", None, 10, seed="2026-11-07"))
    other = _refs(resurface.review(env, "random", None, 10, seed="2026-11-08"))

    assert first == again
    assert first != other


def test_review_spaced_is_stable_for_a_fixed_clock(env):
    _seed_many(env, 10)

    first = _refs(resurface.review(env, "spaced", None, 5, today=TODAY))
    again = _refs(resurface.review(env, "spaced", None, 5, today=TODAY))
    assert first == again


def test_review_spaced_prefers_never_shown(env):
    shot = _screenshot(env, "2025-11-07")
    shown = _highlight(env, shot, "Already reviewed.", day="2025-11-07")
    _highlight(env, shot, "Never reviewed.", day="2025-11-07")
    _review_row(env, "highlight", shown, next_due="2099-01-01")

    result = resurface.review(env, "spaced", None, 10, today=TODAY)
    assert [i["text"] for i in result["items"]] == ["Never reviewed."]
    assert result["total_candidates"] == 1


def test_review_spaced_excludes_items_not_yet_due(env):
    shot = _screenshot(env, "2025-11-07")
    due = _highlight(env, shot, "Due today.", day="2025-11-07")
    early = _highlight(env, shot, "Not yet.", day="2025-11-07")
    _review_row(env, "highlight", due, next_due=TODAY)
    _review_row(env, "highlight", early, next_due="2026-12-01")

    result = resurface.review(env, "spaced", None, 10, today=TODAY)
    assert [i["text"] for i in result["items"]] == ["Due today."]


def test_review_spaced_favours_older_material_first(env):
    shot = _screenshot(env, "2025-11-07")
    newer = _highlight(env, shot, "Newer.", day="2026-01-01")
    older = _highlight(env, shot, "Older.", day="2024-01-01")

    refs = _refs(resurface.review(env, "spaced", None, 10, today=TODAY))
    assert refs == [("highlight", older), ("highlight", newer)]


def test_review_excludes_soft_deleted_items(env):
    shot = _screenshot(env, "2025-11-07")
    _highlight(env, shot, "Still here.", day="2025-11-07")
    _highlight(env, shot, "Deleted.", day="2025-11-07", deleted="2026-11-08 09:00:00")
    _clipping(env, "cp-live", "Live excerpt.", created=_epoch("2025-11-07"))
    _clipping(env, "cp-dead", "", created=_epoch("2025-11-07"), deleted=1)

    for strategy in resurface.STRATEGIES:
        result = resurface.review(env, strategy, None, 10, seed="s", today=TODAY)
        assert {i["text"] for i in result["items"]} == {"Still here.", "Live excerpt."}
        assert result["total_candidates"] == 2


def test_review_defaults_to_passages_not_screenshots(env):
    shot = _screenshot(env, "2025-11-07", "A page of OCR")
    _highlight(env, shot, "A sentence worth keeping.", day="2025-11-07")

    assert resurface.review(env, "spaced", None, 10, today=TODAY)["kinds"] == [
        "highlight", "clipping"
    ]
    assert _refs(resurface.review(env, "spaced", None, 10, today=TODAY)) == [("highlight", 1)]
    screens = resurface.review(env, "spaced", ["screenshot"], 10, today=TODAY)
    assert _refs(screens) == [("screenshot", shot)]


def test_review_item_carries_its_schedule(env):
    shot = _screenshot(env, "2025-11-07")
    ref = _highlight(env, shot, "Already reviewed.", day="2025-11-07")
    _review_row(env, "highlight", ref, next_due="2026-11-01")

    item = resurface.review(env, "spaced", None, 10, today=TODAY)["items"][0]
    assert item["shown_count"] == 2
    assert item["next_due"] == "2026-11-01"
    assert item["last_shown"] == "2026-10-24 18:02:11"
    assert item["book_slug"] == "pastoral"


def test_review_ignores_a_review_log_row_whose_item_was_purged(env):
    _review_row(env, "highlight", 999, next_due="2020-01-01")
    result = resurface.review(env, "spaced", None, 10, today=TODAY)
    assert result["items"] == []
    assert result["total_candidates"] == 0


# ------------------------------------------------------------------ #
# Scheduling                                                           #
# ------------------------------------------------------------------ #

def test_mark_seen_good_doubles_the_interval(env):
    expected = [(1, "2026-11-09"), (2, "2026-11-10"), (4, "2026-11-12"), (8, "2026-11-16")]
    for interval, due in expected:
        result = resurface.mark_seen(env, "highlight", 41, "good", today=TODAY)
        assert (result["interval_days"], result["next_due"]) == (interval, due)
    assert result["shown_count"] == 4


def test_mark_seen_caps_the_interval_at_a_year(env):
    _review_row(env, "highlight", 41, next_due="2026-01-01", interval=300)

    result = resurface.mark_seen(env, "highlight", 41, "good", today=TODAY)
    assert result["interval_days"] == resurface.INTERVAL_CAP_DAYS == 365
    assert result["next_due"] == "2027-11-08"


def test_mark_seen_again_resets_the_interval_to_one(env):
    _review_row(env, "highlight", 41, next_due="2026-11-01", interval=64)

    result = resurface.mark_seen(env, "highlight", 41, "again", today=TODAY)
    assert result["interval_days"] == 1
    assert result["next_due"] == "2026-11-09"
    assert result["shown_count"] == 3


def test_mark_seen_is_an_upsert(env):
    first = resurface.mark_seen(env, "clipping", 7, "good", today=TODAY)
    second = resurface.mark_seen(env, "clipping", 7, "good", today=TODAY)

    assert (first["shown_count"], second["shown_count"]) == (1, 2)
    with sqlite3.connect(env) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM review_log WHERE kind = 'clipping' AND ref_id = 7"
        ).fetchone()[0]
    assert rows == 1


def test_mark_seen_rejects_bad_input(env):
    with pytest.raises(ValueError):
        resurface.mark_seen(env, "highlight", 1, "easy", today=TODAY)
    with pytest.raises(ValueError):
        resurface.mark_seen(env, "bookmark", 1, "good", today=TODAY)


def test_review_log_init_is_idempotent(env):
    with sqlite3.connect(env) as conn:
        for _ in range(3):
            resurface.init_db(conn)
    resurface.mark_seen(env, "highlight", 1, "good", today=TODAY)
    with sqlite3.connect(env) as conn:
        assert conn.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == 1


# ------------------------------------------------------------------ #
# HTTP                                                                #
# ------------------------------------------------------------------ #

def test_review_endpoint_round_trip(env, client):
    shot = _screenshot(env, "2025-11-07")
    ref = _highlight(env, shot, "A sentence worth keeping.", day="2025-11-07")

    queue = client.get("/api/review?strategy=spaced&limit=5").json()
    assert queue["items"][0]["ref_id"] == ref

    seen = client.post(f"/api/review/highlight/{ref}/seen", json={"rating": "good"})
    assert seen.status_code == 200
    assert seen.json()["interval_days"] == 1
    assert seen.json()["next_due"] == (date.today() + timedelta(days=1)).isoformat()


def test_review_endpoint_rejects_bad_input(env, client):
    assert client.get("/api/review?strategy=sm2").status_code == 400
    assert client.get("/api/review?limit=0").status_code == 422
    assert client.post("/api/review/bookmark/1/seen", json={"rating": "good"}).status_code == 400
    assert client.post("/api/review/highlight/1/seen", json={"rating": "easy"}).status_code == 400


def test_resurfacing_is_refused_on_the_public_sync_host(env, client, monkeypatch):
    monkeypatch.setattr(ks, "_PUBLIC_SYNC_HOST", "sync.example.org")
    pub = {"host": "sync.example.org"}

    assert client.get("/api/on-this-day", headers=pub).status_code == 404
    assert client.get("/api/review", headers=pub).status_code == 404
    assert client.post("/api/review/highlight/1/seen", json={"rating": "good"},
                       headers=pub).status_code == 404
    assert client.get("/api/on-this-day").status_code == 200
    assert client.get("/api/review").status_code == 200


def test_utc_epoch_helper_matches_sqlite(env):
    """The clipping day comes from SQLite's own unixepoch conversion."""
    created = _epoch("2025-11-07")
    _clipping(env, "cp-1", "An excerpt.", created=created)
    with sqlite3.connect(env) as conn:
        day = conn.execute(
            "SELECT date(created_at, 'unixepoch') FROM clippings"
        ).fetchone()[0]
    assert day == "2025-11-07"
    assert datetime.fromtimestamp(created, timezone.utc).date().isoformat() == day
