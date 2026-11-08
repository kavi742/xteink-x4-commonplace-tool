# Plan 2: Reading Data

This document expands Phase 2 of [IDEAS.md](IDEAS.md) into an implementable design. It covers seven
work items: derived reading sessions (2.1), a daily rollup table (2.2), a `books` table with read
status and reading attempts that absorbs `tbr_books` (2.3), a real statistics page (2.4), a writing
log that cites clippings and screenshots (2.5), persistence for the screenshot-to-clipping link
(2.6), and undoable deleting and editing of reading data (2.7). The goal is to stop treating
`progress_updates` as the query surface for every reading statistic, to give the app a stable book
identity that screenshots, clippings, progress, and the TBR list all agree on, and to give the
archive somewhere to write.

**Plan numbers are not build phases.** `TESTING.md` numbers the historical build phases 1 to 11
(Foundation, Device discovery, Status display, Screenshot archiving, KOReader sync, Dedup state,
Vault writer, Observability, Web UI, Integration, Clipping sync). All of those are done. The
"Plan 1 / Plan 2 / Plan 3" numbering in this document and its siblings
(`PLAN-1-QUICK-WINS.md`, `PLAN-3-POLISH.md`) is a separate forward-looking split of the BookOrbit
audit in `IDEAS.md`. Item 2.1 here has nothing to do with Phase 2 in `TESTING.md`.

---

## Ordering and dependencies

| Order | Item | Depends on | Why |
| --- | --- | --- | --- |
| 1 | **2.3** `books` table, slug columns | nothing | Every other item keys on `book_slug`. Doing this first means 2.1 can write a resolved slug on every session instead of backfilling one later. |
| 2 | **2.1** `reading_sessions` | 2.3 | Needs `document_aliases.book_slug` to label a session, and `books` to open and close reading attempts. |
| 3 | **2.2** `reading_daily_stats` | 2.1 | The rollup is a pure aggregate over `reading_sessions`. It cannot exist before sessions do. |
| 4 | **2.6** clipping link persistence | 2.3 | Uses `synced_screenshots.book_slug` to index a book's screenshots without the current Python-side canonicalisation pass. Otherwise independent of 2.1 and 2.2, so it can land in parallel with them. |
| 5 | **2.5** writing log | 2.3, softly 2.6 | Keys on `book_slug`. Reuses the soft-delete, undo, and trash pattern from Plan 1 item 1.3, so land 1.3 first if both are in flight. 2.6 is a soft dependency: the reference picker shows a clipping next to the screenshot it came from, which is one stored lookup after 2.6 and a full `_BookIndex` rebuild per keystroke before it. The item works without 2.6; it is just slower. |
| 6 | **2.4** statistics page | 2.1, 2.2, 2.3 | Every endpoint reads the rollup, the sessions table, or `reading_attempts`. Building it first would mean building it twice. |
| 7 | **2.7** deleting and editing reading data | 2.1, 2.2, 2.3, 2.5, plus Plan 1 item 1.3 and Plan 3 item 3.7 | It re-derives all three derived tables, so they have to exist and be correct before anything can invalidate them. It reuses Plan 1 item 1.3's `undo_log`, reversal registry, and clipping tombstones rather than defining its own, and Plan 3 item 3.7's dry run is the book-delete preview. 2.5 is a hard dependency because a delete that is unaware of citations silently edits writing. Land 1.3 before any of it and 3.7 before the delete button ships. |

Within 2.3, the `tbr_books` fold is the last step: create and backfill `books` from screenshots and
aliases first, confirm it looks right, then fold the TBR rows in.

Named seams to sibling plans, not designed here:

- Plan 1 item 1.3 (soft delete, restore, `/trash`) defines the `deleted_at` + restore + purge
  pattern. Item 2.5 reuses it verbatim for `writing_entries`, and 2.3 reuses it for `books` so the
  TBR fold migration cannot resurrect a book the user deleted.
- Plan 3 items 3.1 (book metadata) and 3.2 (covers) need a stable book row to hang metadata and a
  `cover_path` on. The `books` table from 2.3 is that row. `books.cover_path` and
  `books.total_pages` are declared here and left empty for Plan 3 to populate.
- Plan 3 item 3.9 (share poster) needs per-day and per-book reading totals that do not require a
  full log scan in the browser. `reading_sessions` from 2.1 and `reading_daily_stats` from 2.2 are
  that input.
- Plan 1 item 1.3's `undo_log`, `xteink_service/undo.py` reversal registry, `POST /api/undo`, and
  `UndoToast.svelte` are the undo mechanism for item 2.7. 2.7 registers three new kinds against that
  registry with a longer TTL and adds no second table, endpoint, or token format. Its clipping
  handling reuses 1.3's tombstone plus `trashed_text` rather than deleting rows.
- Plan 3 item 3.7 (preview-then-apply) is the book-delete preview in 2.7. The dry-run contract and
  the "writes nothing" property are 3.7's; 2.7 only supplies the plan it renders.
- Plan 1 item 1.1 (FTS5 search) must index writing entries. `search_docs.kind` gains a fifth value,
  `writing`, whose `body` is the entry body with every anchor token replaced by its
  `writing_refs.snapshot`. That is what makes a search for a quoted phrase return both the clipping
  and the entry that cites it, which is the behaviour a commonplace book wants. 1.1 does not design
  this; it only has to leave room for a fifth kind, which its `kinds` facet already does.
- Plan 1 item 1.2 (export) must export writing entries with their references resolved. `include`
  gains `writing`, and an exported entry expands its anchors exactly as the vault render does, so
  the exported file is self-contained rather than full of unresolvable tokens.
- Plan 3 item 3.7's golden render fixture must be captured **after** item 2.5 lands. 3.7 says to
  capture the fixture from the current implementation before refactoring, and that still holds; the
  point here is that "current" has to mean "including writing entries". A fixture captured from a
  note format that has no writing spans in it would make the first dry run after 2.5 report every
  note containing an entry as changed.

---

## Data model decision

Every item below needs an answer to one question first: `reading_sessions`, `reading_daily_stats`,
`books`, and `reading_attempts` all derive from data that currently lives in two separate SQLite
files, and the plan has to pick one home.

### The two databases today

| | `state.db` (env `STATE_DB`) | `koreader.db` (env `KOREADER_DB`) |
| --- | --- | --- |
| Opened by | [xteink_service/state.py](xteink_service/state.py), [xteink_service/clippings.py](xteink_service/clippings.py), [xteink_service/book_pages.py](xteink_service/book_pages.py), [xteink_service/api.py](xteink_service/api.py) `_state_conn()` | [xteink_service/koreader_sync.py](xteink_service/koreader_sync.py) `ProgressStore`, [xteink_service/api.py](xteink_service/api.py) `_koreader_conn()` |
| Tables | `synced_screenshots`, `highlights`, `tbr_books`, `document_aliases`, `clippings`, `book_pages` | `progress_updates`, `devices` |
| Written by | archiver, clipping sync, UI CRUD | the kosync hot path only |
| Rebuildable | partly (the vault markdown and PNG sidecars are a second copy) | no, it is the only record of reading history |

`api.py` already combines them in Python rather than in SQL: `_pages_by_hash()` joins
`document_aliases` to `book_pages` in a dict, `list_books()` merges a screenshot query and a
progress query on `canonical_book_slug` in a loop, and `_hashes_for_book()` canonicalises every
alias title in Python because there is no slug column to query.

### Decision

**All new tables live in `state.db`. No `ATTACH DATABASE`. Cross-database reads stay Python-side,
and the backfill streams one document at a time.**

Reasons, weighed:

1. **`koreader.db` is the device-facing write path and should stay boring.** Every
   `POST /syncs/progress` opens a fresh connection in `ProgressStore._connect()` and writes
   `progress_updates`. Putting derived tables there means a UI request that rebuilds statistics
   takes write locks on the database the X4 is trying to push into. `state.db` is already written
   from the kosync path (`_record_client_title()` constructs a `SyncState` on every push), so using
   it for derivation adds no new cross-database coupling that is not already there.
2. **The join targets are all in `state.db`.** A session row needs `book_slug` (from
   `document_aliases`), `pages_delta` (from `book_pages`), and it feeds `books` and
   `reading_attempts`, which in turn need `tbr_books`, `synced_screenshots`, and `clippings`. Six of
   the seven inputs are in `state.db`. Only `progress_updates` is not.
3. **`ATTACH` would create cross-database transactions.** Writing `reading_sessions` in `state.db`
   while `koreader.db` is attached puts both files in one transaction, which makes SQLite write a
   super-journal next to the main database and take a read lock on the attached file for the
   duration of the commit. The payoff would be one tidier SQL statement. The cost is a new failure
   mode on the device-facing file. Not worth it.
   `ATTACH` with a read-only URI (`file:...?mode=ro`) also requires the main connection to be
   opened with `uri=True`, which changes how every path containing `?` or `#` is parsed. The test
   databases come from `tempfile.mktemp()` and the production ones from compose, so this would work
   today, but it is a sharp edge for no gain.
4. **Backup and restore get simpler, not harder.** `state.db` becomes the single file worth backing
   up for anything hand-curated: titles, notes, writing, statuses, ratings. `koreader.db` stays a
   pure event log. The derived tables living next to the curated data means one file restores a
   coherent picture.
   The failure case is restoring one file and not the other. Because every derived table is a pure
   function of `progress_updates` plus the alias and page tables, the answer is the rebuild
   endpoints specified in each item below (`POST /api/sessions/rebuild`,
   `POST /api/stats/rebuild`, `POST /api/books/rebuild`). After a mismatched restore, run all
   three. Say so in the item backfill sections rather than trying to detect it.
5. **`conftest.py` keeps working unchanged.** It pins `STATE_DB` and `KOREADER_DB` to temp files
   *before* any test module imports `xteink_service.koreader_sync`, because that module builds a
   module-level `ProgressStore` that captures `KOREADER_DB` at import time. Keeping the new code
   out of module-level singletons means nothing new captures a path at import.

### Hard rule for every new module

**No new module may read `STATE_DB` or `KOREADER_DB` at import time.** Resolve paths inside
functions, exactly as `clippings._state_db()` and `api._state_db()` do:

```python
def _state_db() -> str:
    return os.getenv("STATE_DB", "/data/state/state.db")
```

Never this:

```python
_DB = os.getenv("STATE_DB", "/data/state/state.db")   # forbidden: freezes the test path
```

`ProgressStore` is the one existing exception and it is the reason `conftest.py` has to set the
environment before import. Do not add a second one.

### Schema evolution style

There is no migration framework and this plan does not add one. Schema changes follow the style
already in `state.py::SyncState._init_db` and `koreader_sync.py::ProgressStore._init_db`:

```python
def init_db() -> None:
    """Idempotent. Safe to call at the top of every entry point."""
    with _connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ...")
        conn.execute("CREATE INDEX IF NOT EXISTS ...")
        for table, col, typedef in _ADDED_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass  # column already exists
```

Each new module gets its own `init_db()` called at the top of each entry point, following
`clippings.init_db()`. Do not grow `SyncState.__init__`: it runs on every construction, including
inside the kosync hot path via `_record_client_title()`.

### Table placement summary

| Table | Database | Rebuildable from |
| --- | --- | --- |
| `books` | `state.db` | screenshots + aliases + progress + `tbr_books` |
| `reading_attempts` | `state.db` | `reading_sessions` (auto rows only; manual rows are source data) |
| `reading_sessions` | `state.db` | `progress_updates` |
| `reading_daily_stats` | `state.db` | `reading_sessions` |
| `writing_entries` | `state.db` | nothing, this is source data |
| `writing_refs` | `state.db` | partly: the anchors are re-derivable from `writing_entries.body`, but `snapshot` is not derivable from anything and is the reason the table is source data |
| `clipping_screenshot_links` | `state.db` | `clippings` + `synced_screenshots` |
| `settings` | `state.db` | nothing, this is source data |
| `sync_suppressions` | `state.db` | nothing, this is source data |
| new indexes on `progress_updates` | `koreader.db` | n/a |

---

## Work items

### 2.1 Derive `reading_sessions`

#### Why

A reading session does not exist as a concept today. `/api/books/{slug}/reading-calendar` in
[xteink_service/api.py](xteink_service/api.py) counts a "session" as the number of rows in
`progress_updates` for a calendar day, which means a book left open with the sync plugin pushing
every ten minutes reports dozens of sessions for one sitting. `/api/books/{slug}/reading-stats`
reports `"sessions": len(rows)` from the same raw count. Nothing anywhere knows how long a sitting
lasted.

**What derivation can and cannot measure.** The device sends no session or duration data. A derived
session measures **elapsed time between position reports**, nothing more. If the reader puts the X4
down mid-chapter and picks it up twenty minutes later without the sync plugin firing, that twenty
minutes is inside the session. If the reader falls asleep with the device on, that is inside the
session too. It is not attention, and it is not page-turn activity. Every surface that shows
`minutes` must label it as time with the book open, and `duration_s = 0` sessions (a single push)
must be excluded from any pace calculation. State this in the UI copy, not just in a comment.

#### Data model

In `state.db`:

```sql
CREATE TABLE IF NOT EXISTS reading_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document        TEXT    NOT NULL,
    device_key      TEXT    NOT NULL DEFAULT '',
    device          TEXT    NOT NULL DEFAULT '',
    book_slug       TEXT    NOT NULL DEFAULT '',
    started_at      INTEGER NOT NULL,
    ended_at        INTEGER NOT NULL,
    duration_s      INTEGER NOT NULL DEFAULT 0,
    start_pct       REAL    NOT NULL DEFAULT 0,
    end_pct         REAL    NOT NULL DEFAULT 0,
    pct_delta       REAL    NOT NULL DEFAULT 0,
    pages_delta     INTEGER,
    local_date      TEXT    NOT NULL DEFAULT '',
    updates         INTEGER NOT NULL DEFAULT 0,
    first_update_id INTEGER NOT NULL DEFAULT 0,
    last_update_id  INTEGER NOT NULL DEFAULT 0,
    gap_threshold_s INTEGER NOT NULL DEFAULT 1800,
    derived_at      INTEGER NOT NULL DEFAULT 0,
    UNIQUE(document, device_key, started_at)
);

CREATE INDEX IF NOT EXISTS idx_sessions_slug_start
    ON reading_sessions (book_slug, started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_date
    ON reading_sessions (local_date);
CREATE INDEX IF NOT EXISTS idx_sessions_doc_end
    ON reading_sessions (document, device_key, ended_at);
```

In `koreader.db`, added to `ProgressStore._init_db()` so the per-document scan is indexed:

```sql
CREATE INDEX IF NOT EXISTS idx_progress_doc_ts
    ON progress_updates (document, timestamp, id);
CREATE INDEX IF NOT EXISTS idx_progress_ts
    ON progress_updates (timestamp);
```

Column notes:

- `device_key` is `COALESCE(NULLIF(device_id, ''), device, '')`, the same expression the `devices`
  backfill already uses in `ProgressStore._init_db`. `device` is the display name.
- `pages_delta` is `page_at(end_pct, total) - page_at(start_pct, total)` using `pages.page_at` and
  the `book_pages` row for the resolved title. It is `NULL` when no page count is cached.
  **It is an estimate of book pages, not device pages.** `CLAUDE.md` is explicit that no whole-book
  page number is ever synced: the rich `position` object's `page` and `pages` are within the current
  spine item and layout-dependent, so `book_pages.py` inference stays the only source.
- `local_date` is `datetime.fromtimestamp(started_at).date().isoformat()`, matching
  `api.reading_calendar()` and the vault. A session that crosses midnight belongs to the date it
  started. The container's `TZ` (America/Toronto in `docker-compose.yml`) defines "local".
- `gap_threshold_s` is stored per row so a later threshold change is visible in the data and the
  rebuild endpoint can tell which rows were derived under the old value.

#### Derivation algorithm

New module `xteink_service/reading.py`.

```python
SESSION_GAP_S = 1800        # 30 minutes
FINISHED_PCT = 0.95         # matches api.reading_log / api.reading_stats
```

**Grouping key: `(document, device_key)`.** Not `document` alone. Two CrossPoint readers holding
byte-identical files push under the same `document` hash with the same hardcoded
`device_id = "crosspoint-reader"` unless one of them sets `X-Device-Name`
(`koreader_sync._device_override`). Grouping by document alone would interleave two readers into one
impossible session with a negative `pct_delta`. Splitting on `device_key` keeps each reader's
stream separate and makes `device` well defined on the row. Two devices reading the same book under
*different* hashes already split naturally, and are rejoined at query time by `book_slug`, which is
how `_linked_documents` already links them.

**Ordering: `ORDER BY timestamp, id`.** `progress_updates.timestamp` is server-assigned
(`ProgressStore.upsert` passes `int(datetime.now(timezone.utc).timestamp())`, and the column default
is `strftime('%s','now')`), so it is monotonic with `id` in normal operation. Ties happen: two
pushes inside the same second get the same timestamp. `id` breaks them, and because `id` is unique
the ordering is **total and deterministic**. Never sort by `timestamp` alone.

**Splitting:** walk the ordered rows for one key. Start a new session when
`row.timestamp - prev.timestamp > SESSION_GAP_S`. Otherwise extend the current one.

**Session fields:** `started_at` and `start_pct` come from the first row in the group, `ended_at`
and `end_pct` from the last, `duration_s = ended_at - started_at`, `pct_delta = end_pct - start_pct`
(signed, may be negative when the reader navigated backwards or restarted a book),
`updates = len(group)`.

A single-row group produces a session with `duration_s = 0`. Keep it: it still records that the book
was open that day and it feeds `days_read`. Exclude it from pace, not from counts.

**Out-of-order and late rows.** The incremental path never appends to an existing session in place.
It recomputes a bounded window:

```
anchor = SELECT MAX(timestamp) FROM progress_updates
          WHERE document = ? AND device_key_expr = ?
            AND timestamp <= (t - SESSION_GAP_S)
      (0 when there is no such row)

DELETE FROM reading_sessions
 WHERE document = ? AND device_key = ? AND ended_at > anchor;

re-derive from rows WHERE timestamp > anchor, insert.
```

`anchor` is the newest update that is separated from `t` by more than the gap, so no session
boundary at or before `anchor` can move no matter what arrives after it. That makes the window the
smallest provably safe one. A row that lands inside an already-derived window (clock skew, a restore,
a hand-inserted row) is handled by the same code path, because the window is recomputed from the
source rows rather than patched.

**Idempotency.** For a fixed window the derivation is a pure function of the ordered row list, the
ordering is total (`timestamp, id`), and the write is `DELETE` over the exact window followed by
`INSERT` of the recomputed rows. Re-deriving a day therefore produces byte-identical rows apart from
`id` and `derived_at`. Tests compare on the natural key `(document, device_key, started_at)` and the
value columns, never on `id`.

**Recompute triggers beyond a new push.** `book_slug` and `pages_delta` are resolved at derivation
time from tables that can change later:

| Event | Hook | Action |
| --- | --- | --- |
| New progress row | `koreader_sync.put_progress` after `_store.upsert` | `reading.extend_for_update(document, device_key, timestamp)` |
| Alias set by hand | `api.set_alias` | `reading.relabel_document(doc_hash)`: update `book_slug` on that document's sessions, recompute affected days |
| Page count resolved | `book_pages.resolve_book_pages` after `_store(...)` | `reading.refresh_pages_for_title(title)`: recompute `pages_delta` for that book's sessions |
| Title volunteered on the hot path | none needed | `_record_client_title()` runs *before* `_store.upsert()` in `put_progress`, so the alias already exists when the session is derived |

#### API

```
GET /api/sessions
    ?book=<slug>        optional, filters on book_slug
    &from=YYYY-MM-DD    optional, inclusive, on local_date
    &to=YYYY-MM-DD      optional, inclusive, on local_date
    &limit=100          1..500
    &offset=0
->
[
  {
    "id": 412,
    "document": "a1b2c3...",
    "book_slug": "the-hidden-keys",
    "book_title": "Hidden Keys, The - Andre Alexis",
    "device": "CrossPoint",
    "started_at": 1793203200,
    "ended_at": 1793206500,
    "duration_s": 3300,
    "minutes": 55.0,
    "local_date": "2026-11-03",
    "start_pct": 0.412,
    "end_pct": 0.465,
    "pct_delta": 0.053,
    "percent_read": 5.3,
    "pages_delta": 9,
    "updates": 7
  }
]
```

```
POST /api/sessions/rebuild
body: { "from": "YYYY-MM-DD" | null, "book": "<slug>" | null }
->
{ "documents": 14, "sessions": 903, "days_recomputed": 221, "duration_ms": 740 }
```

`from` is interpreted against `progress_updates.timestamp`. `null` means rebuild everything.
After rebuilding sessions the endpoint recomputes `reading_daily_stats` for every touched date (see
2.2), so the two tables cannot drift apart.

#### Service changes

New file `xteink_service/reading.py`:

- `init_db()` - the SQL above, idempotent.
- `_state_db()` / `_koreader_db()` / `_connect()` - env read inside the function, never at import.
- `derive_sessions(rows, *, document, device_key, book_slug, total_pages) -> list[dict]` - pure, no
  I/O, no database. This is the function the idempotency tests target directly.
- `extend_for_update(document, device_key, timestamp) -> None` - the bounded recompute above.
- `rebuild(since=None, book=None) -> dict` - streams `SELECT DISTINCT document FROM progress_updates`
  and then one document's rows at a time, so peak memory is one book's history rather than the whole
  log.
- `relabel_document(doc_hash)`, `refresh_pages_for_title(title)`.
- `recompute_days(dates)` - delegates to the 2.2 rollup.

Modified:

- [xteink_service/koreader_sync.py](xteink_service/koreader_sync.py) `put_progress()`: after
  `record = _store.upsert(...)` and before `_write_progress_to_vault(update)`, call a single
  `_maintain_reading_data(update, record)` helper (see "Hot path budget" below).
- [xteink_service/koreader_sync.py](xteink_service/koreader_sync.py) `ProgressStore._init_db()`:
  add the two indexes.
- [xteink_service/api.py](xteink_service/api.py) `reading_calendar()` and `book_reading_stats()`:
  read `reading_sessions` by `book_slug` instead of scanning `progress_updates`.
  `reading-calendar` keeps its exact response shape so
  [web/src/lib/components/ReadingCalendar.svelte](web/src/lib/components/ReadingCalendar.svelte)
  does not change, and gains `minutes` and `sessions` now meaning real sittings.
  `book-reading-stats` keeps its keys and `sessions` changes meaning from "pushes" to "sittings",
  which is the point.
- [xteink_service/book_pages.py](xteink_service/book_pages.py) `resolve_book_pages()`: call
  `reading.refresh_pages_for_title(title)` after `_store(...)` succeeds.

#### UI

- `web/src/lib/api.ts`: add `ReadingSession`, `api.sessions.list(params)`,
  `api.sessions.rebuild(body)`. Extend `ReadingCalendarDay` with `minutes: number`.
- `web/src/routes/books/[book]/+page.svelte`: the existing `.reading-stats` block gains a
  "time with book" stat sourced from the sum of session minutes, labelled
  `time open` with a title attribute explaining it is elapsed time between position reports.
- `web/src/lib/components/ReadingCalendar.svelte`: `tip()` adds minutes when present.

#### Backfill and migration

1. Deploy. `init_db()` creates the empty table on first request.
2. `POST /api/sessions/rebuild` with `{"from": null, "book": null}`.
3. Safe to re-run at any time: the rebuild deletes and re-derives per `(document, device_key)` and
   recomputes the affected dates. Running it twice produces identical rows.
4. There is no backward migration to write. Dropping the feature means dropping the table, because
   nothing else depends on it except 2.2 and 2.4.

#### Risks

- **The gap threshold is a guess.** 30 minutes splits a commute from an evening, but it merges a
  lunch break into one long sitting. It is stored per row so a change is auditable, and the rebuild
  endpoint re-derives everything. Do not expose it as a setting until there is evidence it needs
  tuning.
- **Minutes will look inflated** for anyone who leaves the device on. The honest labelling above is
  the mitigation, not a cleverer algorithm.
- **A device with a wrong clock does not matter** because the timestamp is server-assigned. A
  *server* clock jump does matter, and would split or merge sessions around it. Acceptable.
- **Backwards navigation produces negative `pct_delta`.** Stored signed, clamped to zero only in the
  rollup. Any consumer that forgets to clamp will show negative pages read.

---

### 2.2 `reading_daily_stats` rollup

#### Why

`GET /api/reading-stats` in [xteink_service/api.py](xteink_service/api.py) runs
`SELECT document, percentage, timestamp FROM progress_updates` with no `WHERE` and no `LIMIT`, loads
every row into a Python dict keyed by document, and recomputes today, week, month, and year
aggregates on every single request. The reading log page calls it on every load
([web/src/routes/log/+page.ts](web/src/routes/log/+page.ts)). That is the single worst scan in the
application and it grows forever.

#### Data model

```sql
CREATE TABLE IF NOT EXISTS reading_daily_stats (
    local_date      TEXT    PRIMARY KEY,
    sessions        INTEGER NOT NULL DEFAULT 0,
    minutes         REAL    NOT NULL DEFAULT 0,
    pct_sum         REAL    NOT NULL DEFAULT 0,
    pages           REAL    NOT NULL DEFAULT 0,
    books_touched   INTEGER NOT NULL DEFAULT 0,
    books_finished  INTEGER NOT NULL DEFAULT 0,
    first_at        INTEGER,
    last_at         INTEGER,
    updated_at      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_daily_stats_updated
    ON reading_daily_stats (updated_at);
```

`pct_sum` is in fraction units (0.0 to 1.0 per book), matching `progress_updates.percentage`, and is
converted to a display percentage only at the API boundary. That keeps it consistent with every
existing stored value.

**There is deliberately no second per-book-per-day rollup table.** Per-book views
(`/api/books/{slug}/reading-calendar`, the per-book trace in 2.4) read `reading_sessions` directly
through `idx_sessions_slug_start`. One book's sessions are bounded by that book's reading history,
which is small, and a second rollup would be a second thing to keep in sync for no measured win.

#### Derivation algorithm

Recompute a whole day from `reading_sessions`, never increment counters. Incrementing is what makes
rollups drift; a full day recompute is idempotent by construction and costs one indexed scan of one
day's sessions.

```sql
-- 1. clear the day
DELETE FROM reading_daily_stats WHERE local_date = :day;

-- 2. recompute from sessions; HAVING keeps empty days out of the table
INSERT INTO reading_daily_stats
       (local_date, sessions, minutes, pct_sum, pages,
        books_touched, books_finished, first_at, last_at, updated_at)
SELECT :day,
       COUNT(*),
       COALESCE(SUM(duration_s), 0) / 60.0,
       COALESCE(SUM(max(pct_delta, 0.0)), 0),
       COALESCE(SUM(max(COALESCE(pages_delta, 0), 0)), 0),
       COUNT(DISTINCT CASE WHEN book_slug <> '' THEN book_slug END),
       0,
       MIN(started_at),
       MAX(ended_at),
       :now
  FROM reading_sessions
 WHERE local_date = :day
HAVING COUNT(*) > 0;

-- 3. books finished that day, from reading_attempts (2.3)
UPDATE reading_daily_stats
   SET books_finished = (
         SELECT COUNT(*)
           FROM reading_attempts
          WHERE finished_at IS NOT NULL
            AND deleted_at IS NULL
            AND date(finished_at, 'unixepoch', 'localtime') = :day)
 WHERE local_date = :day;
```

`max(a, b)` with two arguments is SQLite's scalar max, so `SUM(max(pct_delta, 0.0))` clamps negative
deltas (backwards navigation, re-read resets) before summing. `date(..., 'localtime')` uses the
container `TZ`, the same one `datetime.fromtimestamp()` uses when `local_date` is written, so the
two agree.

**Idempotency:** delete-then-insert over a single primary key, computed entirely from
`reading_sessions`. Re-running for the same day writes the same row apart from `updated_at`. Tests
compare all columns except `updated_at`.

**Which days to recompute:** `extend_for_update()` returns the set of `local_date` values it
inserted or deleted, and the caller recomputes exactly those. A session that moves across midnight
during a recompute touches both dates, and both are in the returned set because the delete and the
insert each contribute their dates.

#### API

`GET /api/reading-stats` keeps its path and every existing key so
[web/src/lib/api.ts](web/src/lib/api.ts) `ReadingStats` and
[web/src/routes/log/+page.svelte](web/src/routes/log/+page.svelte) keep working. Two additive
blocks:

```json
{
  "books":      { "started": 61, "in_progress": 4, "finished": 57 },
  "read_pct":   { "today": 5.3, "week": 22.1, "month": 140.6 },
  "pages_read": { "today": 9,   "week": 61,   "month": 388 },
  "books_read": { "today": 0, "week": 1, "month": 3, "year": 29, "all_time": 57 },
  "minutes":    { "today": 55, "week": 310, "month": 1420 },
  "streak":     { "current": 6, "longest": 41, "last_active": "2026-11-07" }
}
```

Served by:

- `read_pct`, `pages_read`, `minutes`: `SELECT SUM(...) FROM reading_daily_stats WHERE local_date >= ?`
  with three bounds (today, Monday of this week, first of this month) computed in Python exactly as
  the current code does.
- `books`: `SELECT status, COUNT(*) FROM books WHERE deleted_at IS NULL GROUP BY status` (2.3).
- `books_read`: `SELECT COUNT(*) FROM reading_attempts WHERE finished_at >= ? AND deleted_at IS NULL`.
- `streak`: `SELECT local_date FROM reading_daily_stats ORDER BY local_date` and a single pass. The
  row count is one per active day, so this is bounded by days read, not by pushes.

```
POST /api/stats/rebuild
body: { "from": "YYYY-MM-DD" | null }
-> { "days": 221, "duration_ms": 95 }
```

Recomputes every date present in `reading_sessions` at or after `from`.

#### Service changes

In `xteink_service/reading.py`:

- `recompute_day(conn, day: str) -> None` - the three statements above, inside the caller's
  transaction.
- `recompute_days(days: Iterable[str]) -> int` - opens one connection, loops.
- `rebuild_stats(since: str | None) -> dict`.

In [xteink_service/api.py](xteink_service/api.py):

- `reading_stats()` rewritten against `reading_daily_stats`, `books`, and `reading_attempts`. The
  `by_doc` dict, the `for doc, pts in by_doc.items()` loop, and the unbounded
  `SELECT document, percentage, timestamp FROM progress_updates` all go.

#### UI

- `web/src/lib/api.ts`: extend `ReadingStats` with `minutes` and `streak`.
- `web/src/routes/log/+page.svelte`: one more `.stats-group` for "Time" (today / week / month) and
  a streak line. No structural change.

#### Backfill and migration

Covered by `POST /api/sessions/rebuild`, which recomputes every touched date. `POST /api/stats/rebuild`
exists for the case where sessions are already correct and only the rollup is suspect. Both are
idempotent and safe to re-run.

#### Risks

- **Rollup drift** if any code path writes `reading_sessions` without recomputing the day. Mitigate
  by making `recompute_day` the only writer of `reading_daily_stats` and by having every session
  writer return its touched dates. The rebuild endpoint is the repair tool.
- **Timezone changes.** Changing the container `TZ` re-buckets every day. `POST /api/sessions/rebuild`
  fixes it, since `local_date` is rewritten from the raw timestamps.
- **`books_finished` depends on 2.3.** Until `reading_attempts` exists the column stays 0. Land 2.3
  first, as the ordering table says.

---

### 2.3 `books`, read status, and reading attempts

#### Why

There is no book row. A book is implied by three unrelated things: a `book_title` string in
`synced_screenshots`, a `title` string in `document_aliases`, and a row in `tbr_books` that has no
relationship at all to the book you then read. `api.list_books()` reconciles the first two by
canonicalising every title in Python on every request, `api._book_titles_for_slug()` scans
`SELECT DISTINCT book_title FROM synced_screenshots` and canonicalises each one, and
`koreader_sync._linked_documents()` reads the entire `document_aliases` table and canonicalises
every row just to answer "which hashes are the same book". A `books` table plus two stored slug
columns replaces all of that with indexed lookups.

It also fixes the re-read ambiguity. `CLAUDE.md` notes that a percentage resetting toward 0 after a
finish is currently indistinguishable from a new book. `reading_attempts` is where that distinction
lives.

#### Data model

```sql
CREATE TABLE IF NOT EXISTS books (
    book_slug      TEXT    PRIMARY KEY,
    title          TEXT    NOT NULL DEFAULT '',
    author         TEXT    NOT NULL DEFAULT '',
    status         TEXT    NOT NULL DEFAULT 'unread',
    status_source  TEXT    NOT NULL DEFAULT 'auto',
    status_at      INTEGER,
    rating         INTEGER,
    on_list        INTEGER NOT NULL DEFAULT 0,
    source_url     TEXT    NOT NULL DEFAULT '',
    notes          TEXT    NOT NULL DEFAULT '',
    sort_order     INTEGER NOT NULL DEFAULT 0,
    tbr_id         INTEGER,
    total_pages    INTEGER,
    cover_path     TEXT    NOT NULL DEFAULT '',
    first_seen     INTEGER,
    last_activity  INTEGER,
    created_at     INTEGER NOT NULL DEFAULT 0,
    updated_at     INTEGER NOT NULL DEFAULT 0,
    deleted_at     INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_books_tbr_id
    ON books (tbr_id) WHERE tbr_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_books_status
    ON books (status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_books_on_list
    ON books (on_list, sort_order) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_books_activity
    ON books (last_activity);

CREATE TABLE IF NOT EXISTS reading_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_slug   TEXT    NOT NULL,
    attempt_no  INTEGER NOT NULL DEFAULT 1,
    started_at  INTEGER,
    finished_at INTEGER,
    outcome     TEXT    NOT NULL DEFAULT 'in_progress',
    rating      INTEGER,
    notes       TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT 'auto',
    start_pct   REAL    NOT NULL DEFAULT 0,
    end_pct     REAL    NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL DEFAULT 0,
    updated_at  INTEGER NOT NULL DEFAULT 0,
    deleted_at  INTEGER,
    UNIQUE(book_slug, attempt_no)
);

CREATE INDEX IF NOT EXISTS idx_attempts_slug
    ON reading_attempts (book_slug, attempt_no);
CREATE INDEX IF NOT EXISTS idx_attempts_finished
    ON reading_attempts (finished_at) WHERE finished_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
```

Stored slugs, replacing the Python-side canonicalisation scans. Each one goes in a
`try` / `except sqlite3.OperationalError` block:

```sql
ALTER TABLE synced_screenshots ADD COLUMN book_slug TEXT DEFAULT '';
ALTER TABLE document_aliases   ADD COLUMN book_slug TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_screenshots_slug ON synced_screenshots (book_slug);
CREATE INDEX IF NOT EXISTS idx_aliases_slug     ON document_aliases (book_slug);
```

**Status values** (8 states, exactly as `IDEAS.md` specifies):

| Status | Settable by `auto` | Meaning |
| --- | --- | --- |
| `unread` | yes | Known to the archive (screenshots or clippings) but no reading progress. |
| `want_to_read` | yes, only from the TBR fold | On the list, not started. |
| `reading` | yes | Attempt 1 is open. |
| `on_hold` | **no** | Manual only. |
| `rereading` | yes | Attempt 2 or later is open. |
| `read` | yes | The newest attempt is finished. |
| `skimmed` | **no** | Manual only. |
| `abandoned` | **no** | Manual only. |

`on_hold`, `skimmed`, and `abandoned` are manual only on purpose. Deriving `on_hold` would require
a "no activity for N days" sweep, which means a scheduler, which this service does not have and
should not grow for a label.

**`status_source` rule:** an `auto` derivation may only overwrite a row whose `status_source` is
`auto`. A `manual` status is never changed by the device. `POST /api/books/{slug}/status/auto`
clears the override by setting `status_source = 'auto'` and immediately re-deriving.

**`on_list`** marks "the human put this on the reading list". It exists so that after the TBR fold,
a book with `status = 'read'` that came from the old `done` section is distinguishable from the
fifty-seven books finished before a TBR list ever existed. Without it the `/tbr` page's Done section
would suddenly list every book ever read. Invariant maintained by the endpoints:
`status = 'want_to_read'` implies `on_list = 1`.

#### Derivation algorithm: attempts and auto status

In `xteink_service/books.py`:

```python
FINISHED_PCT = 0.95   # same threshold as api.reading_log and api.reading_stats
RESTART_PCT  = 0.10   # a new attempt must start at or below this
```

Called after each session is written for a book:

1. Find the newest non-deleted attempt for `book_slug`.
2. No attempt yet: open attempt 1 with `started_at = session.started_at`,
   `start_pct = session.start_pct`, `outcome = 'in_progress'`, `source = 'auto'`.
3. Open attempt exists and `session.end_pct >= FINISHED_PCT`: set `end_pct`,
   `finished_at` = the timestamp of the first update in that session at or above `FINISHED_PCT`,
   `outcome = 'finished'`.
4. Newest attempt is `finished` and `session.start_pct <= RESTART_PCT`: open
   `attempt_no + 1`. **This is the only re-read signal available.** A percentage drop without a
   prior finish is backwards navigation, not a re-read, and must not open an attempt.
5. Otherwise just extend the open attempt's `end_pct` with `max(end_pct, session.end_pct)` and
   `updated_at`.

Auto status then follows directly: no attempts and no progress gives `unread` (or `want_to_read`
when `on_list = 1`), an open attempt 1 gives `reading`, an open attempt 2 or later gives
`rereading`, and a finished newest attempt gives `read`. Apply only when `status_source = 'auto'`.

**Idempotency:** `reading_attempts` is keyed `UNIQUE(book_slug, attempt_no)` and every write is an
upsert of that key, so replaying the same sessions converges on the same rows. Manual rows carry
`source = 'manual'` and the auto path never modifies a manual row's `rating`, `notes`, or `outcome`.
`POST /api/books/rebuild` deletes only `source = 'auto'` attempts before re-deriving.

#### API

```
GET /api/books
-> [
  {
    "book_slug": "the-hidden-keys",
    "book_title": "The-Hidden-Keys",
    "author": "Andre Alexis",
    "screenshot_count": 25,
    "last_synced": "2026-07-04 18:11:02",
    "last_date": "2026-07-04",
    "percentage_display": 46.5,
    "last_read_at": 1793206500,
    "status": "reading",
    "status_source": "auto",
    "on_list": 0,
    "rating": null,
    "attempt_count": 1,
    "total_pages": 198
  }
]
```

Every key the current response has is preserved, so
[web/src/lib/components/BookCard.svelte](web/src/lib/components/BookCard.svelte) and the sidebar
index in [web/src/routes/+layout.svelte](web/src/routes/+layout.svelte) keep working untouched. The
implementation becomes a single query against `books` joined to `synced_screenshots` counts, with no
Python merge loop.

```
GET  /api/books/{slug}
-> the object above, plus "notes", "source_url", "sort_order", "first_seen", "created_at"

PUT  /api/books/{slug}
body: { "title"?, "author"?, "status"?, "rating"?, "notes"?,
        "source_url"?, "on_list"?, "sort_order"? }
-> { "updated": "the-hidden-keys", "status": "on_hold", "status_source": "manual" }
```

Sending `status` sets `status_source = 'manual'` and `status_at = now`. `status` must be one of the
eight values; anything else is a 400. `rating` must be 1..5 or null.

```
POST /api/books/{slug}/status/auto
-> { "book_slug": "...", "status": "reading", "status_source": "auto" }

GET    /api/books/{slug}/attempts
-> [ { "id": 7, "attempt_no": 2, "started_at": 1793203200, "finished_at": null,
       "outcome": "in_progress", "rating": null, "notes": "", "source": "auto",
       "start_pct": 0.02, "end_pct": 0.31 } ]

POST   /api/books/{slug}/attempts
body: { "started_at"?, "finished_at"?, "outcome"?, "rating"?, "notes"? }
-> the created attempt, with source = "manual"

PUT    /api/attempts/{id}
body: { "started_at"?, "finished_at"?, "outcome"?, "rating"?, "notes"? }
-> { "updated": 7 }

DELETE /api/attempts/{id}        soft delete, sets deleted_at
-> { "deleted": 7 }

POST   /api/books/rebuild
body: { "book": "<slug>" | null }
-> { "books": 61, "attempts": 68, "slugs_backfilled": 1842, "duration_ms": 410 }
```

The TBR compatibility shim keeps all four existing paths and their exact response shapes
(`GET /api/tbr`, `POST /api/tbr`, `PUT /api/tbr/{id}`, `DELETE /api/tbr/{id}`), reading and writing
`books` instead of `tbr_books`. Field mapping on the way out:

| `books` | `tbr_books` field returned |
| --- | --- |
| `tbr_id` if set, else `rowid`-stable surrogate from `book_slug` hash | `id` |
| `title`, `author`, `source_url`, `notes`, `sort_order` | same names |
| `created_at` as ISO | `added_at` |

Status mapping both ways:

| `books.status` | TBR `status` out | TBR `status` in |
| --- | --- | --- |
| `want_to_read` | `queued` | `queued` -> `want_to_read` |
| `unread`, `abandoned`, `skimmed` | `queued` | |
| `reading`, `rereading`, `on_hold` | `reading` | `reading` -> `reading` |
| `read` | `done` | `done` -> `read` |

`GET /api/tbr` selects `WHERE deleted_at IS NULL AND on_list = 1`, ordered by the same
`CASE status ... END, sort_order, book_slug` the current endpoint uses. `POST /api/tbr` sets
`on_list = 1`, `status = 'want_to_read'`, `status_source = 'manual'`.
`DELETE /api/tbr/{id}` sets `on_list = 0`, it does **not** delete the book row, because the book may
have screenshots and reading history attached to it.

The `id` returned by the shim must be stable across restarts. Use `books.tbr_id` where it exists
(migrated rows keep their old integer id, so existing `/tbr` bookmarks and the Svelte `(book.id)`
keyed each-block keep working), and for rows created after the fold allocate a new `tbr_id` from
`SELECT COALESCE(MAX(tbr_id), 0) + 1 FROM books`. That keeps one integer namespace with no second
table.

#### Service changes

New file `xteink_service/books.py`:

- `init_db()` - all SQL above plus the `ALTER TABLE` pairs in `try` / `except sqlite3.OperationalError`.
- `touch(conn, book_slug, *, title=None, author=None, when=None)` - upsert a row, update
  `last_activity` and `first_seen`, never touch `status` when `status_source = 'manual'`.
- `apply_session(conn, session) -> None` - the attempt transitions above.
- `derive_status(conn, book_slug) -> str`.
- `backfill_slugs(conn) -> int` - fills `synced_screenshots.book_slug` and
  `document_aliases.book_slug` using `canonical_book_slug`, in batches, `WHERE book_slug IS NULL OR
  book_slug = ''` unless `force=True`.
- `backfill_books(conn) -> int` - one row per distinct slug from screenshots, aliases with progress,
  and `tbr_books`.
- `fold_tbr(conn) -> dict` - see below.
- `rebuild(book=None) -> dict`.

Modified:

- [xteink_service/state.py](xteink_service/state.py) `mark_synced()`: also write `book_slug`.
- [xteink_service/state.py](xteink_service/state.py) `set_title()`: also write `book_slug`.
- [xteink_service/api.py](xteink_service/api.py) `_book_titles_for_slug()`:
  `SELECT DISTINCT book_title FROM synced_screenshots WHERE book_slug = ?`.
- [xteink_service/api.py](xteink_service/api.py) `_hashes_for_book()`:
  `SELECT hash FROM document_aliases WHERE book_slug = ?`.
- [xteink_service/api.py](xteink_service/api.py) `list_books()`: single query against `books`.
- [xteink_service/api.py](xteink_service/api.py) `set_alias()`: write `book_slug`, then call
  `reading.relabel_document()` and `books.touch()`.
- [xteink_service/api.py](xteink_service/api.py) TBR endpoints: rewritten as the shim.
  `_ensure_tbr()` is deleted.
- [xteink_service/koreader_sync.py](xteink_service/koreader_sync.py) `_linked_documents()`:
  ```sql
  SELECT hash FROM document_aliases
   WHERE book_slug <> ''
     AND book_slug = (SELECT book_slug FROM document_aliases WHERE hash = ?)
  ```
  Falls back to the current Python scan when the subquery returns empty or NULL, so a database that
  has not been backfilled yet still links correctly.
- [xteink_service/clippings.py](xteink_service/clippings.py) `_slug_by_hash()`: read the stored
  column.

#### UI

- `web/src/lib/api.ts`: extend `Book`; add `BookDetail`, `ReadingAttempt`; add
  `api.books.get/update/clearOverride`, `api.books.attempts.*`, `api.books.rebuild`.
  `TbrBook` is unchanged.
- New `web/src/lib/components/BookStatusControl.svelte`: a select of the eight statuses, a 1-5 star
  rating, and a "revert to automatic" button shown only when `status_source === 'manual'`.
- New `web/src/lib/components/AttemptList.svelte`: the attempt rows with per-attempt dates, outcome,
  and rating.
- `web/src/routes/books/[book]/+page.svelte`: mount both above the reading calendar; add a fourth
  tab is **not** needed, attempts go in the header block.
- `web/src/routes/tbr/+page.svelte` and `web/src/lib/components/TbrRow.svelte`: **unchanged.** They
  talk to the shim.

#### Backfill and migration

This is the one genuinely destructive change in the plan, so the procedure is explicit.

**Step 1, additive, no risk.** `books.init_db()` creates the tables and adds the two `book_slug`
columns. Existing rows get `''`.

**Step 2, slug backfill.** `backfill_slugs()` fills `synced_screenshots.book_slug` and
`document_aliases.book_slug` from `canonical_book_slug(title)`. Idempotent: it only touches rows
where the column is empty, unless called with `force=True` (which `POST /api/books/rebuild` uses,
so a future change to `canonical_book_slug` can be applied by rerunning it).

**Step 3, books backfill.** `backfill_books()` inserts one row per distinct slug:

```sql
-- screenshots
INSERT OR IGNORE INTO books (book_slug, title, status, status_source, first_seen, created_at, updated_at)
SELECT book_slug, MIN(book_title), 'unread', 'auto',
       CAST(strftime('%s', MIN(synced_at)) AS INTEGER),
       CAST(strftime('%s', MIN(synced_at)) AS INTEGER), :now
  FROM synced_screenshots
 WHERE book_slug <> ''
 GROUP BY book_slug;

-- aliases that actually have progress (same rule list_books() uses today)
-- documents come from koreader.db, so the hash set is passed in from Python
INSERT OR IGNORE INTO books (book_slug, title, status, status_source, first_seen, created_at, updated_at)
SELECT book_slug, MIN(title), 'unread', 'auto', :now, :now, :now
  FROM document_aliases
 WHERE book_slug <> '' AND hash IN (<synced hashes>)
 GROUP BY book_slug;
```

**Step 4, the TBR fold.** Runs in Python because `canonical_book_slug` is a Python function.

```python
def fold_tbr(conn) -> dict:
    """Idempotent. Keyed on tbr_id, so re-running changes nothing."""
    rows = conn.execute(
        "SELECT id, title, author, source_url, notes, status, sort_order, added_at "
        "FROM tbr_books"
    ).fetchall()
    for r in rows:
        slug = canonical_book_slug(r["title"])
        if not slug:
            continue
        added = int(...)  # strftime('%s', added_at), falling back to now
        status = {"queued": "want_to_read", "reading": "reading", "done": "read"}.get(
            r["status"], "want_to_read")
        conn.execute(
            """INSERT OR IGNORE INTO books
                   (book_slug, title, author, source_url, notes, status, status_source,
                    on_list, sort_order, tbr_id, first_seen, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?)""",
            (slug, r["title"], r["author"], r["source_url"], r["notes"],
             status, "manual", r["sort_order"], r["id"], added, added, now))
        # The slug already existed (a book with screenshots or progress): merge the
        # list membership and any field the book row does not already have.
        conn.execute(
            """UPDATE books
                  SET on_list    = 1,
                      tbr_id     = COALESCE(tbr_id, ?),
                      source_url = CASE WHEN source_url = '' THEN ? ELSE source_url END,
                      notes      = CASE WHEN notes      = '' THEN ? ELSE notes      END,
                      author     = CASE WHEN author     = '' THEN ? ELSE author     END,
                      sort_order = CASE WHEN sort_order = 0  THEN ? ELSE sort_order END,
                      updated_at = ?
                WHERE book_slug = ? AND deleted_at IS NULL""",
            (r["id"], r["source_url"], r["notes"], r["author"], r["sort_order"], now, slug))
```

Why this is idempotent and lossless:

- `INSERT OR IGNORE` on the `book_slug` primary key never duplicates.
- The `UPDATE` only fills fields that are still empty, and `COALESCE(tbr_id, ?)` only claims an id
  the row does not already have. Running it twice is a no-op.
- `WHERE ... deleted_at IS NULL` means a book the user soft-deleted after the fold is **not**
  resurrected by a re-run. This is why `books` uses the Plan 1 item 1.3 soft-delete pattern rather
  than hard deletes.
- The `queued` to `want_to_read` mapping keeps `status_source = 'manual'`, because the human put it
  there. For `reading` and `done`, `fold_tbr` then calls `derive_status()` once and, if the derived
  status differs and the book has progress rows, sets the derived value with
  `status_source = 'auto'`. The device knows better than the stale TBR flag for books it has
  actually tracked. Books with no progress keep the manual mapped value.

**Step 5, `tbr_books` is left on disk and goes read-only.** Nothing writes it after the fold. It is
the rollback copy. Dropping the table is a separate chore for a later release, not part of this
plan. `api._ensure_tbr()` is removed so no code path can recreate or write it.

**Order of operations on deploy:**

1. Deploy. `init_db()` runs on the first request to any `/api/books*` or `/api/tbr*` route.
2. `POST /api/books/rebuild` with `{"book": null}`. This runs steps 2, 3, 4 and re-derives statuses
   and auto attempts.
3. `POST /api/sessions/rebuild` (2.1), which now labels sessions with `book_slug` and opens attempts.
4. Spot check `/tbr` and `/books`.

Re-running any of these at any time is safe.

#### Risks

- **`canonical_book_slug` collisions.** Two genuinely different books whose titles canonicalise to
  the same slug merge into one row. This risk already exists everywhere in the app (`list_books()`,
  `_hashes_for_book()`, `_linked_documents()`), so the fold does not create it, but the fold makes
  it durable. Mitigation: `GET /api/books/{slug}` returns the list of contributing titles so a
  collision is visible, and `PUT /api/aliases/{hash}` can be used to rename one of them apart.
- **TBR shim id drift.** If `tbr_books` is ever dropped before every row has a `tbr_id` on the
  corresponding `books` row, `/tbr` ids shift. Mitigation: do not drop the table in this plan, and
  assert in a test that every `on_list = 1` row has a non-null `tbr_id`.
- **The fold freezes human intent.** A TBR row marked `queued` becomes manual `want_to_read` forever
  until the user clicks "revert to automatic". That is the correct default (never silently overwrite
  a human) but it will surprise someone. The button is the mitigation and it must be visible.
- **Partial indexes** require SQLite 3.8.0 or newer. Python 3.12 bundles far newer than that, and
  the Docker image is Debian-based, so this is safe, but it is the one syntax here that is not
  ancient.

---

### 2.4 A real statistics page

#### Why

There is no statistics page. `/log` shows four small stat groups computed by the full-table scan in
`api.reading_stats()` ([web/src/routes/log/+page.svelte](web/src/routes/log/+page.svelte)), and the
only visualisation in the app is the per-book heatmap in
[web/src/lib/components/ReadingCalendar.svelte](web/src/lib/components/ReadingCalendar.svelte),
which scales its bins to `maxRead` within that one book. A relative scale makes a quiet year look as
busy as a heavy one, which is exactly the failure mode fixed bins exist to prevent.

#### Data model

No new tables. Everything reads `reading_daily_stats`, `reading_sessions`, `reading_attempts`, and
`books`. One settings row for the yearly goal:

```sql
-- key = 'reading_goal:2026', value = '36'
INSERT INTO settings (key, value) VALUES (?, ?)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value;
```

#### Algorithm notes

**Fixed heatmap bins.** Bin on **minutes with the book open per day**, with absolute edges, shared
between backend and frontend as one constant list:

```
level 0: 0 minutes (no row)
level 1: 0 < m <= 15
level 2: 15 < m <= 30
level 3: 30 < m <= 60
level 4: m > 60
```

**Low-confidence guard.** `MIN_ACTIVE_DAYS = 14`. When `reading_daily_stats` has fewer than 14 rows
in the requested range, the response carries `"low_confidence": true` and the UI renders the grid in
a muted single-hue ramp with a short note ("not enough days yet to read much into this"). Streak,
pace, peak hours, and the year projection are all returned but flagged with the same field. Do not
hide the numbers, just mark them.

**Streak.** One pass over `SELECT local_date FROM reading_daily_stats ORDER BY local_date`. Current
streak counts back from today, allowing today itself to be missing (so the streak does not appear to
break at 00:01). Longest streak is the longest run of consecutive dates. Bounded by active days.

**Pace.** `SUM(pages) / (SUM(minutes) / 60.0)` over sessions with `duration_s > 0`, restricted to
books that have a `book_pages` row. Returned as `pages_per_hour`, with `pages_basis` naming how many
books contributed, because a page count from
[xteink_service/book_pages.py](xteink_service/book_pages.py) may be a word-count estimate. Also
return `pct_per_hour`, which needs no page count and is the more honest number.

**Peak hours and favourite days.** 24 and 7 buckets over `reading_sessions.started_at`, weighted by
`duration_s` where `duration_s > 0` and by session count otherwise. A session that spans hours is
attributed to the hour it started, which is the simple answer and is what the label should say.

**Completion timeline.** `reading_attempts` where `finished_at IS NOT NULL`, grouped by month.

**Year projection.** `finished_so_far / fraction_of_year_elapsed`, rounded, with the goal from
`settings`. Flagged `low_confidence` before March 1 or when fewer than 3 books have been finished
in the year.

**Per-book trace.** `SELECT started_at, end_pct, pages_delta FROM reading_sessions WHERE book_slug = ?
ORDER BY started_at`, which is one indexed scan over one book.

#### API

```
GET /api/stats/overview?year=2026
->
{
  "year": 2026,
  "low_confidence": false,
  "active_days": 221,
  "minutes_total": 18420,
  "pct_total": 61.4,
  "pages_total": 12880,
  "books_finished": 29,
  "streak":  { "current": 6, "longest": 41, "last_active": "2026-11-07" },
  "pace":    { "pages_per_hour": 41.9, "pct_per_hour": 23.1, "pages_basis": 24 },
  "goal":    { "target": 36, "finished": 29, "projected": 33, "on_track": false }
}

GET /api/stats/heatmap?from=2026-01-01&to=2026-12-31
->
{
  "low_confidence": false,
  "bins": [0, 15, 30, 60],
  "unit": "minutes",
  "days": [
    { "date": "2026-11-03", "minutes": 55.0, "percent_read": 5.3,
      "pages": 9, "sessions": 2, "books": 1, "level": 3 }
  ]
}

GET /api/stats/rhythm?year=2026
->
{
  "low_confidence": false,
  "hours": [ { "hour": 0, "minutes": 12.0, "sessions": 1 }, ... 24 entries ],
  "weekdays": [ { "weekday": 0, "label": "Mon", "minutes": 410.0, "sessions": 38 }, ... 7 entries ]
}

GET /api/stats/completions?year=2026
->
{
  "months": [ { "month": "2026-01", "finished": 3 }, ... ],
  "books": [ { "book_slug": "the-hidden-keys", "title": "The Hidden Keys",
               "attempt_no": 1, "started_at": 1790000000,
               "finished_at": 1793206500, "days": 37, "rating": 4 } ]
}

GET /api/books/{slug}/trace
->
{
  "total_pages": 198,
  "points": [ { "at": 1790000000, "end_pct": 0.04, "page": 8, "session_id": 401 } ]
}

GET /api/stats/goal?year=2026    -> { "year": 2026, "target": 36 }
PUT /api/stats/goal              body { "year": 2026, "target": 36 } -> { "year": 2026, "target": 36 }
```

All of these go under `/api/`, which means they are refused on `PUBLIC_SYNC_HOST` by
`koreader_sync._restrict_public_sync_host`. **Do not add any of them to `_PUBLIC_ALLOWED`.** That
tuple is `("/syncs", "/users", "/health", "/api/v1/clippings")` and must stay exactly that.

#### Service changes

New file `xteink_service/stats.py` with the six query functions, each taking an open connection, so
they are directly unit-testable without a `TestClient`. Routes live in
[xteink_service/api.py](xteink_service/api.py) next to the existing reading endpoints and are thin.

#### UI

- New route `web/src/routes/stats/+page.svelte` and `+page.ts` (parallel `Promise.all` loads with
  `.catch(() => null)`, matching [web/src/routes/log/+page.ts](web/src/routes/log/+page.ts)).
- New `web/src/lib/heatmap.ts`: `HEATMAP_BINS`, `heatmapLevel(value, bins)`. Imported by both the
  stats page and `ReadingCalendar.svelte`.
- New `web/src/lib/components/YearHeatmap.svelte`: the full-year grid. Reuses the month-grid
  construction already written in `ReadingCalendar.svelte`; extract `buildMonth()` into
  `web/src/lib/calendar.ts` and have both components import it rather than duplicating it.
- New `web/src/lib/components/BarRow.svelte`: a labelled horizontal bar, used for both peak hours
  and favourite days.
- New `web/src/lib/components/TraceChart.svelte`: a single `<svg>` with a `<polyline>` for the
  per-book progress trace, mounted on `web/src/routes/books/[book]/+page.svelte`.
- `web/src/routes/+layout.svelte`: add `{ href: '/stats', label: 'Statistics' }` to `navLinks`, and
  a bottom-nav entry.
- `web/src/styles/app.css`: add the heat ramp as tokens so charts recolour with the theme:
  ```css
  --heat-0: var(--bg-sidebar);
  --heat-1: rgba(166, 218, 149, .28);
  --heat-2: rgba(166, 218, 149, .50);
  --heat-3: rgba(166, 218, 149, .75);
  --heat-4: rgba(166, 218, 149, 1);
  --heat-muted: var(--border);   /* low-confidence ramp */
  ```
  `ReadingCalendar.svelte` currently hardcodes those four `rgba()` values in its `<style>` block;
  move it onto the tokens in the same change. Plan 1 item 1.6 restructures the whole palette into
  OKLCH; this ramp is written so that it only has to change in one place when 1.6 lands.

#### Charting library decision

**Recommendation: do not add one.** The four visuals this plan needs are a calendar grid of `<div>`
cells (already written and working), two bar charts over 24 and 7 fixed buckets (a `<div>` with a
width percentage), one line trace (a single `<svg><polyline>`), and a month bar for completions
(same bar component). None of them needs an axis engine, a scale library, or a tooltip framework.
The app currently ships **zero** runtime chart dependencies, and `web/package.json` has only the
three Capacitor packages in `dependencies`. Keeping it that way is worth more than the convenience.

If a library is wanted anyway, the pick is **LayerCake** (`npm i layercake`), and the justification
against the alternatives:

| Library | Approx. gzipped | Renders to | Reads CSS custom properties at runtime |
| --- | --- | --- | --- |
| **LayerCake** | ~5 kB | SVG, you supply the marks | **Yes, natively.** `stroke: var(--heat-4)` on an SVG element just works, and recolours on theme change with no JS. |
| uPlot | ~7 kB | Canvas | No. Colours must be read with `getComputedStyle()` and passed as strings, then the chart must be redrawn on every theme change. |
| Chart.js | ~70 kB core, more with adapters | Canvas | Same canvas problem, ten times the size. |
| ECharts | ~330 kB | Canvas | No. Far too large for this app. |

LayerCake is Svelte-native (v8 supports Svelte 5, which this app uses with
`runes: true` in [web/vite.config.ts](web/vite.config.ts)), and because it renders SVG it satisfies
the "charts recolour with the theme" requirement for free, which is the whole reason BookOrbit needs
a `useChartTokens.ts` helper on the Vue side.

**Either way, note the cost:** adding it changes `web/package.json` and `web/package-lock.json`, so
the Docker build pulls a new dependency, and `web/android/` has to be re-synced with
`npx cap sync` before the next Capacitor build. That is why the default answer is no.

#### Backfill and migration

None. Every endpoint is read-only over tables built by 2.1, 2.2, and 2.3. The only writable state is
the goal row in `settings`, which is created on first `PUT`.

#### Risks

- **Numbers that look wrong will be believed.** Minutes are elapsed time, pages are an estimate from
  [xteink_service/book_pages.py](xteink_service/book_pages.py) (possibly a word-count guess), and
  pace divides one by the other. Every one of those three must be labelled in the UI, and
  `pages_basis` exists so the page-derived numbers can be suppressed when too few books have a real
  Open Library count.
- **A new top-level page is scope.** `SCOPE.md` discipline applies: this is one route with one
  loader, no new dependency, and it replaces existing scattered stats rather than adding a parallel
  surface.
- **The fixed bins will need tuning.** They are a single exported constant in
  `web/src/lib/heatmap.ts` and are echoed in the API response as `bins`, so a change is one edit and
  the client does not have to be redeployed in lockstep.

---

### 2.5 Writing log

#### Why

The requirement is "a writing log: the ability to write about the book as I go, and use the
clippings and screenshots as examples in the writing". That is what a commonplace tool is for, and
it is the one thing this app cannot do. It captures material well: screenshots are downloaded,
OCR'd, and archived, clippings sync off the device, highlights are drawn on the images. Nothing can
be written **about** any of it. The only free-text fields are `synced_screenshots.user_notes` and
per-clipping notes, each attached to a single captured item, which is the wrong shape. A thought
about the ending is not an annotation on a page.

So this item is not a notes field. It is long-form markdown that **cites** captured material
inline, so a paragraph can quote the clipping it is arguing with, show the screenshot that clipping
came from, and keep that quotation readable years later when the clipping underneath it has been
retitled, re-synced under a different hash, or deleted outright.

The vault side already fits. `VaultWriter.append_to_daily_note` and `update_book_timeline` maintain
`## YYYY-MM-DD` headings in `Books/<Title>.md`, and `append_to_daily_note` already embeds images as
`![[<Title>/<file>.png]]`, so a dated entry and its screenshot citations have an obvious home and an
existing syntax.

#### Data model

Two tables. `writing_entries` holds the prose, `writing_refs` holds the citations. They are source
data, not derived, and neither is rebuildable from anything else.

```sql
CREATE TABLE IF NOT EXISTS writing_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    book_slug     TEXT    NOT NULL,
    entry_date    TEXT    NOT NULL,
    title         TEXT    NOT NULL DEFAULT '',
    body          TEXT    NOT NULL DEFAULT '',
    position_pct  REAL,
    created_at    INTEGER NOT NULL DEFAULT 0,
    updated_at    INTEGER NOT NULL DEFAULT 0,
    deleted_at    INTEGER,
    vault_written INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_writing_book_date
    ON writing_entries (book_slug, entry_date) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_writing_deleted
    ON writing_entries (deleted_at) WHERE deleted_at IS NOT NULL;
```

`entry_date` is a local `YYYY-MM-DD` string so it groups with screenshots
(`synced_screenshots.sync_date`) and sessions (`reading_sessions.local_date`) without conversion.
`title` is optional and is rendered as an `###` heading inside the vault span when present.
`position_pct` is a fraction (0.0 to 1.0), matching `progress_updates.percentage`, and is nullable
because most entries will not have one. `body` must be non-empty after trimming; a body consisting
only of anchors is allowed, because a page of collected quotations with no commentary is a
legitimate commonplace entry. Enforce that in the DTO, not in SQL.

`deleted_at` plus restore plus purge is Plan 1 item 1.3's pattern, used verbatim. The `/trash` page
from that item lists writing entries alongside highlights and clippings. **This document does not
design that page.**

```sql
CREATE TABLE IF NOT EXISTS writing_refs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id     INTEGER NOT NULL,
    kind         TEXT    NOT NULL,
    ref_id       TEXT    NOT NULL,
    document     TEXT    NOT NULL DEFAULT '',
    snapshot     TEXT    NOT NULL DEFAULT '',
    image_path   TEXT    NOT NULL DEFAULT '',
    position     INTEGER NOT NULL DEFAULT 0,
    source_state TEXT    NOT NULL DEFAULT 'live',
    created_at   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_writing_refs_entry
    ON writing_refs (entry_id, position);
CREATE INDEX IF NOT EXISTS idx_writing_refs_source
    ON writing_refs (kind, ref_id);
CREATE INDEX IF NOT EXISTS idx_writing_refs_state
    ON writing_refs (source_state) WHERE source_state <> 'live';
```

No foreign key, matching the rest of the schema and for the same reason 2.6 gives: SQLite does not
enforce one without `PRAGMA foreign_keys=ON`, and nothing sets it. More importantly here, a foreign
key would be actively wrong. The whole point of `writing_refs` is that the row it points at is
allowed to disappear.

Column notes:

- `kind` is `clipping`, `screenshot`, or `highlight`. Validate in the DTO.
- `ref_id` is `TEXT` for all three kinds. For a clipping it holds `clippings.clip_id`, the
  device-assigned string id, because that is the only clipping identifier that survives a re-upload
  under a different document hash, which is exactly the case `refresh` below has to handle. For a
  screenshot it holds `synced_screenshots.id` and for a highlight `highlights.id`, each as a decimal
  string. One column serves all three kinds rather than three nullable typed columns.
- `document` is the clipping's document hash and is empty for the other two kinds. A clipping's
  identity is `UNIQUE(document, clip_id)`, so the hash is needed to resolve it exactly, and it is
  allowed to change (see drift below).
- `image_path` is `synced_screenshots.vault_png_path` for a `screenshot` ref, and for a `highlight`
  ref the `vault_png_path` of its parent screenshot. It is the path the vault render embeds.
- `position` is the ordinal of the ref within its entry, assigned by order of first appearance of
  the anchor in the body. It is what makes "the second quotation in this entry" a stable idea for
  export and search without re-parsing the body.
- `snapshot` is the quoted text copied at citation time. It is argued for below.

#### Anchors and snapshots

**Anchor tokens.** `writing_entries.body` is markdown containing inline anchors of the form
`((ref:<writing_refs.id>))`, for example `((ref:40))`. The token is deliberate, not arbitrary:

- It is **markdown-safe**. Parentheses carry no block or inline meaning in markdown outside a link
  destination, so a renderer that knows nothing about this app leaves the token alone instead of
  eating it, mangling it, or treating it as emphasis.
- It **does not collide with Obsidian**. Obsidian owns `[[...]]` for links and `![[...]]` for
  embeds, and the vault already contains both. A bracket-based token would be ambiguous in the one
  place the output has to live.
- It **survives a round trip through a plain textarea**. The composer is a `<textarea>` holding raw
  markdown, so the token has to be plain printable ASCII that a user can see, select, cut, paste,
  and retype without any editor assistance. That rules out zero-width markers, HTML comments inside
  prose, and anything that needs a rich-text editor to stay intact. It also means the app needs no
  editor dependency at all.

The regex is `\(\(ref:(\d+)\)\)`. A literal `((ref:12))` in prose is escaped as `\((ref:12))`, and
the parser skips a backslash-escaped token.

**Snapshots are the crux of this design.** Each citation keeps its own copy of the quoted text in
`writing_refs.snapshot`. The alternative, storing only a pointer and resolving the text at render
time, is what every "link to your highlights" feature does, and it is wrong here.

The reason is specific to this system. The cited rows are not stable:

- A clipping is a tombstone away from gone. `clippings.delete_clipping()` sets `deleted = 1` and
  `text = ''`, by design, because that is what makes the device drop the id on its next pass.
  A pointer-only citation would render as an empty blockquote the moment that happens.
- A clipping's document hash changes under the user's feet. `CLAUDE.md` records that this device ran
  on Match Method = Filename until roughly 2026-09-27 and on Binary since, and that switching back
  makes the device re-upload its whole local store under a new hash. A pointer keyed on
  `(document, clip_id)` silently stops resolving on a settings change the user made for unrelated
  reasons.
- Screenshot OCR text is hand-corrected. `api.update_screenshot()` writes `ocr_corrected`, so the
  text a quotation was taken from is editable after the fact.
- A book delete removes screenshots and highlights outright (see 2.7's cascade table).

The consequence of snapshotting is the property worth having: **a reader's writing never silently
loses its evidence.** An entry written in 2026 still reads correctly in 2030 whatever happened to
the device, the match method, or the archive underneath it. The framing section above puts it as
`snapshot` not being derivable from anything and being the reason this table is source data rather
than a cache, and that is exactly right. It is the only column in either table that cannot be
reconstructed.

The server, not the client, fills `snapshot`. The add-ref endpoint resolves the source itself and
copies the text, so a stale browser tab cannot write a quotation that was never on screen.

**`source_state`** records the relationship between the snapshot and the row it came from. It is a
stored verdict, recomputed lazily for the refs of the entries a read returns and written when it
changes:

| State | Meaning | Behaviour |
| --- | --- | --- |
| `live` | The cited row still exists and its text still matches the snapshot. | Nothing to show. |
| `deleted` | The cited row is gone: a tombstoned clipping, a deleted screenshot, a deleted book. | The entry renders unchanged, using the snapshot, with a quiet "source removed" affordance next to the quotation. **Nothing cascades into the writing.** |
| `drifted` | The cited row exists but its text no longer matches the snapshot. | The quotation still renders from the snapshot. The UI offers "update to current text", which is the only way the snapshot is ever overwritten. |

Resolution for a `clipping` ref runs in this order, and the second step is what makes drift a real
state rather than a theoretical one:

1. `SELECT ... FROM clippings WHERE document = ? AND clip_id = ? AND deleted = 0`. If the row is
   there, compare `text` to `snapshot`: equal is `live`, different is `drifted`.
2. Otherwise re-resolve by `clip_id` alone across every document hash that resolves to the entry's
   `book_slug`, using `api._hashes_for_book()`, taking the highest `revision` among live rows. This
   is the same "link by canonical slug, not by hash" move `koreader_sync` already makes for
   progress. On a hit, repoint `writing_refs.document` at the surviving hash and compare text.
3. Otherwise `deleted`.

**When drift actually happens here.** Step 2 is the Match Method case. Switch the device from Binary
back to Filename and it re-uploads its whole clipping store under `md5(filename)` hashes while the
old Binary-hashed rows linger as duplicates, which is the migration `xteink_service/cleanup_match_switch.py`
exists to clean up. Without step 2, every citation made before the switch would flip to `deleted`
even though the identical text is sitting in the table under a new hash. With step 2 the ref
re-points and stays `live`. It lands on `drifted` only when the re-uploaded text genuinely differs
from what was quoted, which is the case a reader should actually be told about. The other two drift
sources are an `ocr_corrected` edit behind a `screenshot` ref and a re-saved `highlights.selected_text`.

A `screenshot` ref resolves on `synced_screenshots.id` and compares against
`COALESCE(NULLIF(ocr_corrected, ''), ocr_text)`. A `highlight` ref resolves on `highlights.id` and
compares against `selected_text`. A screenshot ref cites an image first, so its snapshot is the OCR
text at citation time, used as the search text (Plan 1 item 1.1), as the exported caption (item
1.2), and as the fallback rendering when the PNG itself is gone.

#### Vault render

Each entry is written into `Books/<Title>.md` under its `## YYYY-MM-DD` heading, wrapped in HTML
comment markers so an edit rewrites the span in place and a delete removes it:

```markdown
## 2026-11-03

<!-- writing:12 -->
### The ending

Bigland is the only honest voice in it:

> Now, because Bigland did not get home until after dark...
> Chapter 9 · 46.5%

and the page it sits on says the rest:

![[The Hidden Keys/2026-07-04-01.png]]
<!-- /writing:12 -->
```

`write_writing_entry` looks for `<!-- writing:{id} -->` ... `<!-- /writing:{id} -->` and replaces
that span if found, otherwise appends under the heading (creating the heading if missing, exactly as
`update_book_timeline` does). `remove_writing_entry` deletes the span. Writing the same entry twice
therefore produces one block, which is what keeps `POST /api/vault/rebuild` safe to run repeatedly.
This is the same idempotency contract the markers already had before this item grew citations; the
span content changed, the contract did not.

Markers are HTML comments so Obsidian renders nothing. They do not interfere with the `==text==`
highlight marking in `api.create_highlight`, because that only searches for the literal selected
text.

Anchors expand on render, in `position` order, each one replaced in place:

| `kind` | Expansion |
| --- | --- |
| `clipping`, `highlight` | A markdown blockquote of `snapshot`, followed by a locator line inside the same blockquote: the chapter when the source has one, otherwise the position as a percentage, otherwise nothing. |
| `screenshot` | An Obsidian embed, `![[<Title>/<file>.png]]`, derived from `image_path`. This is the same embed syntax `append_to_daily_note` already writes, so the vault has one way of showing an image, not two. |

A `deleted` ref still expands, from its snapshot, with no locator line and a trailing
`*(source removed)*`. A `drifted` ref expands from its snapshot unchanged, because the snapshot is
what the sentence around it was written against. An anchor whose `writing_refs` row does not exist
expands to nothing.

The locator is **not** snapshotted, only the text is. It is read from the live source, so a rebuild
after the source is deleted renders the quotation without its chapter line. That is the deliberate
trade: the quotation is the evidence and must survive, the locator is a convenience, and a second
snapshotted column would need its own drift handling for very little.

#### API

```
GET    /api/books/{slug}/writing?include_deleted=0
-> [
  { "id": 12, "book_slug": "the-hidden-keys", "entry_date": "2026-11-03",
    "title": "The ending",
    "body": "Bigland is the only honest voice in it:\n\n((ref:40))\n\nand the page it sits on says the rest:\n\n((ref:41))",
    "position_pct": 0.465, "position_display": 46.5, "page": 92,
    "created_at": 1793206600, "updated_at": 1793206600, "deleted_at": null,
    "vault_written": 1,
    "refs": [
      { "id": 40, "kind": "clipping", "ref_id": "c-01HX7Q8M5", "document": "a1b2c3...",
        "snapshot": "Now, because Bigland did not get home until after dark...",
        "image_path": "", "position": 0, "source_state": "live",
        "chapter": "Chapter 9", "position_display": 46.5, "screenshot_id": 141,
        "current_text": null },
      { "id": 41, "kind": "screenshot", "ref_id": "141", "document": "",
        "snapshot": "Apple. Now, because Bigland did not get home...",
        "image_path": "Books/The Hidden Keys/2026-07-04-01.png", "position": 1,
        "source_state": "live", "chapter": "", "position_display": null,
        "screenshot_id": 141, "current_text": null }
    ] }
]
```

`chapter`, `position_display`, `screenshot_id`, and `current_text` on a ref are **resolved, not
stored**. `current_text` is `null` except when `source_state` is `drifted`, where it carries the
live text so the UI can show the difference without a second request. `page` on the entry comes from
`pages.page_at(position_pct, total_pages)` and is null without a page count.

```
POST   /api/books/{slug}/writing
body: { "entry_date"?: "YYYY-MM-DD", "title"?: "", "body": "", "position_pct"?: 0.465 }
-> the created entry, with "refs": []

PUT    /api/writing/{id}
body: { "entry_date"?, "title"?, "body"?, "position_pct"? }
-> { "updated": 12, "refs_kept": 2, "refs_dropped": 1, "anchors_stripped": 0 }

DELETE /api/writing/{id}            soft delete
-> { "deleted": 12 }

POST   /api/writing/{id}/restore
-> { "restored": 12 }
```

`entry_date` defaults to today. `position_pct` defaults to the book's current position from
`reading_sessions` when omitted, so "write about where I am" needs no input.

**Anchor reconciliation runs on every create and update, and it goes both ways.** The submitted body
is parsed for anchors, and then:

- A `writing_refs` row for this entry whose anchor is **not** in the submitted body is **deleted**.
  Removing the token from the text is how a citation is removed. That also sweeps up a ref that was
  created by the picker and then never typed into a saved body, so there is no orphan ref and no
  garbage collection job.
- An anchor in the body whose `writing_refs` row does not exist, or exists but belongs to a
  different entry, is **stripped from the stored body**, and counted in `anchors_stripped`. An entry
  cannot cite another entry's ref row, and a hand-typed token cannot invent a citation.
- Surviving refs are renumbered: `position` is reassigned by order of first appearance.

`refs_kept`, `refs_dropped`, and `anchors_stripped` are in the response so the UI can say what
happened rather than silently rewriting what the user typed.

Reference endpoints:

```
GET    /api/books/{slug}/writing/sources?q=bigland&kind=&limit=20
->
{
  "query": "bigland",
  "items": [
    { "kind": "clipping", "ref_id": "c-01HX7Q8M5", "document": "a1b2c3...",
      "text": "Now, because Bigland did not get home until after dark...",
      "chapter": "Chapter 9", "position_pct": 0.465, "screenshot_id": 141,
      "image_path": "Books/The Hidden Keys/2026-07-04-01.png" },
    { "kind": "screenshot", "ref_id": "141", "document": "",
      "text": "Apple. Now, because Bigland did not get home...",
      "chapter": "", "position_pct": null, "screenshot_id": 141,
      "image_path": "Books/The Hidden Keys/2026-07-04-01.png" },
    { "kind": "highlight", "ref_id": "57", "document": "",
      "text": "the house was dark", "chapter": "", "position_pct": null,
      "screenshot_id": 141, "image_path": "Books/The Hidden Keys/2026-07-04-01.png" }
  ]
}

POST   /api/writing/{id}/refs
body: { "kind": "clipping", "ref_id": "c-01HX7Q8M5", "document": "a1b2c3..." }
-> { "id": 40, "anchor": "((ref:40))", "kind": "clipping", "ref_id": "c-01HX7Q8M5",
     "document": "a1b2c3...",
     "snapshot": "Now, because Bigland did not get home until after dark...",
     "image_path": "", "position": 0, "source_state": "live" }

DELETE /api/writing/refs/{ref_id}
-> { "removed": 40 }

POST   /api/writing/refs/{ref_id}/refresh
-> { "id": 40, "source_state": "live", "document": "d4e5f6...",
     "snapshot": "Now, because Bigland did not get home until well after dark...",
     "previous_snapshot": "Now, because Bigland did not get home until after dark..." }
```

`POST /api/writing/{id}/refs` is the picker's insert. The request carries no `snapshot`: the server
resolves the source and copies the text. The response carries `anchor` so the composer has exactly
one thing to insert at the cursor.

`DELETE /api/writing/refs/{ref_id}` exists for an explicit "remove citation" button, which also
strips the token client-side. Reconciliation on the next save would have done the same thing, so the
two paths agree.

`POST /api/writing/refs/{ref_id}/refresh` is the only write that overwrites a snapshot. It returns
409 when `source_state` is `deleted`, because there is nothing to refresh to. It is not registered
with Plan 1 item 1.3's undo log: the list response already carries both `snapshot` and
`current_text` for a drifted ref, so the UI shows the difference before the call rather than offering
to reverse it after, and `previous_snapshot` is in the response for the confirmation toast.

All of these go under `/api/`, so they are refused on `PUBLIC_SYNC_HOST` by
`koreader_sync._restrict_public_sync_host`. **Do not add any of them to `_PUBLIC_ALLOWED`.**

#### Service changes

- [xteink_service/state.py](xteink_service/state.py): add `writing_entries` and `writing_refs` to
  `SyncState._init_db` alongside the other archive tables (both are source data owned by the
  archive, unlike the derived tables which get their own module's `init_db`), plus
  `add_writing_entry`, `list_writing`, `update_writing_entry`, `soft_delete_writing_entry`,
  `restore_writing_entry`, `add_writing_ref`, `list_writing_refs`, and `remove_writing_ref`,
  following the existing `add_highlight` / `list_highlights` style.
- New file `xteink_service/writing.py` for everything that is not plain CRUD: `ANCHOR_RE`,
  `parse_anchors(body)`, `reconcile_refs(conn, entry_id, body)`, `resolve_ref(conn, ref, slug)`,
  `refresh_ref(conn, ref_id)`, `search_sources(conn, slug, q, kind, limit)`, and
  `expand(body, refs, mode)`. Each takes an open connection, so they are directly unit-testable
  without a `TestClient`, matching the reason 2.4 puts its queries in `xteink_service/stats.py`.
  `expand` has two modes, `vault` and `text`. `vault` produces the blockquotes and `![[...]]`
  embeds above. `text` replaces each anchor with its bare snapshot and is what Plan 1 item 1.1
  indexes as `search_docs.kind = 'writing'` and what item 1.2 exports. One expander, three callers.
- `search_sources` queries the current book's clippings, screenshots, and highlights. The clipping
  rows carry a `screenshot_id` so the picker can show a clipping next to the screenshot it came
  from. **Per the ordering table, item 2.6 is a soft dependency here.** After 2.6 that id is one
  stored lookup in `clipping_screenshot_links`. Before 2.6 it is a full `_BookIndex` rebuild in
  `clip_match` per keystroke, which works and is just slow. Debounce the picker either way.
- [xteink_service/vault_writer.py](xteink_service/vault_writer.py): new
  `write_writing_entry(book_title, day, entry_id, rendered)` and
  `remove_writing_entry(book_title, entry_id)`. The rendering lives in `writing.expand`, so
  `VaultWriter` stays a module that writes files and does not learn about citations.
- [xteink_service/api.py](xteink_service/api.py): the nine routes, plus `rebuild_vault()` extended
  to replay writing entries **after** the screenshot rows, so every `![[...]]` embed an entry
  expands to points at a PNG that is already on disk.

#### UI

- `web/src/lib/api.ts`: add `WritingEntry`, `WritingRef`, and `api.writing.*`.
- New `web/src/lib/components/WritingEntry.svelte` (render + inline edit + delete),
  `WritingComposer.svelte` (date, optional title, markdown textarea, picker button), and
  `WritingRefCard.svelte` (one expanded citation: blockquote or image, plus the `drifted` and
  `deleted` affordances).
- New `web/src/lib/components/ReferencePicker.svelte`: a debounced search over
  `/api/books/{slug}/writing/sources`, grouped by kind, each result showing its text and its
  screenshot thumbnail. Choosing one calls `POST /api/writing/{id}/refs` and inserts the returned
  `anchor` at the cursor. The editor is a plain `<textarea>` over raw markdown with a preview pane
  beside it, so no rich-text editor and no new dependency, which is the same answer 2.4 gives about
  charting libraries.
- A `drifted` ref renders with an "update to current text" control showing `snapshot` and
  `current_text`. A `deleted` ref renders the quotation with a muted "source removed" note and no
  control, because there is nothing to do and the writing is fine as it is.
- `web/src/routes/books/[book]/+page.svelte`: a fourth tab, `Writing`, next to Screenshots /
  Highlights / Clippings, following the existing `tab` state pattern. The composer sits above the
  list. Entries render newest date first and, within the Screenshots tab's date groups, an entry for
  that date renders inline above the screenshots for the same `## YYYY-MM-DD`, which is what
  "interleaved with screenshots and progress" means in practice.
- `web/src/routes/books/[book]/+page.ts`: add `api.writing.list(params.book).catch(() => [])` to the
  existing `Promise.all`.

#### Backfill and migration

Nothing to backfill, this is new source data. If `writing_entries` is empty the tab shows the
composer and an empty state.

To get existing entries into the vault after a vault loss, `POST /api/vault/rebuild` replays them
from the database with their anchors expanded, which is the same contract screenshots already have.

Rolling back means dropping both tables. Nothing else depends on them: Plan 1 items 1.1 and 1.2 read
them behind `except sqlite3.OperationalError` like every other optional table in this plan, and
2.7's cascade skips a table it cannot find.

#### Risks

- **Vault and database can diverge** if a user edits `Books/<Title>.md` by hand inside the markers.
  The database wins on the next rebuild. `vault_written` records whether the last write succeeded so
  a failed write is visible, matching how `api.create_highlight` treats a vault write failure as
  non-fatal.
- **The markers are a contract.** If someone later rewrites `VaultWriter` without preserving them,
  rebuild starts duplicating entries. One test asserts the span-replacement behaviour explicitly.
- **Snapshots duplicate text.** A 4 KB clipping cited in five entries is stored six times. The
  device's own decode limits cap a clipping at 4 KB, so the ceiling is small and known, and this
  duplication is the thing being paid for rather than a side effect. Do not "optimise" it into a
  pointer later without reading the snapshot argument above.
- **A refresh is destructive to the snapshot.** It is the one action that can change what a
  quotation says after the fact, which is why it is explicit, user-initiated, shows both texts
  first, and never runs automatically on read. Nothing in the resolution path may write `snapshot`.
- **The anchor token could appear in prose.** `((ref:12))` is unlikely but typable. The escape is
  `\((ref:12))` and one test covers it.

---

### 2.6 Persist the screenshot-to-clipping link

#### Why

[xteink_service/clip_match.py](xteink_service/clip_match.py) builds a full `_BookIndex` (a word
posting list over every screenshot's OCR text for every book involved) on **every** call to
`clippings._decorate()`, which runs on `GET /api/clippings` (up to 1000 rows) and on
`GET /api/books/{slug}/clippings` (every page load of a book's Clippings tab). The result is thrown
away. BookOrbit's `annotation_positions` stores a resolved anchor with a status and a converter
version so a later algorithm change re-runs only the failures, which is exactly the shape needed
here.

The module docstring currently says "Nothing is persisted. Aliases and OCR text are hand-corrected
and stitched-group ids shift when a book is re-synced, so a stored link would go stale." That
reasoning is correct and this item does not ignore it: it answers it with explicit invalidation
triggers, listed below. **Update that docstring as part of the change.** Leaving it in place while
the code does the opposite is worse than either choice alone.

#### Data model

```sql
CREATE TABLE IF NOT EXISTS clipping_screenshot_links (
    clipping_id     INTEGER PRIMARY KEY,
    screenshot_id   INTEGER,
    status          TEXT    NOT NULL DEFAULT 'pending',
    matcher_version INTEGER NOT NULL DEFAULT 0,
    clip_revision   INTEGER NOT NULL DEFAULT 0,
    matched_at      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_clip_links_status
    ON clipping_screenshot_links (status, matcher_version);
CREATE INDEX IF NOT EXISTS idx_clip_links_screenshot
    ON clipping_screenshot_links (screenshot_id) WHERE screenshot_id IS NOT NULL;
```

No foreign key, matching the rest of the schema (`highlights.screenshot_id` declares one but SQLite
does not enforce it without `PRAGMA foreign_keys=ON`, which nothing sets).

#### Algorithm

`MATCHER_VERSION = 1` in [xteink_service/clip_match.py](xteink_service/clip_match.py), bumped by
hand whenever `_PAGE_TOLERANCE`, `_PROBE_WORDS`, `_MIN_PROBE_WORDS`, `_words()`, or `_BookIndex.match()`
changes.

Status is derived from which branch of `_BookIndex.match()` produced the answer. `match()` is
changed to return `(screenshot_id, status)` instead of a bare id:

| Branch in `_BookIndex.match()` | Status | Meaning |
| --- | --- | --- |
| `agreed = position & text` is non-empty | `exact` | Position and OCR text agree. |
| `_sole_image(text)` or `_sole_image(position)` returned an id | `repaired` | One signal only, but it pointed at a single image. |
| Both returned `None`, and the book has at least one indexed screenshot | `failed` | There is something to match against and nothing matched. |
| The book has no indexed screenshots | `pending` | Nothing to match against yet. A later screenshot sync may resolve it. |

**Recompute rule.** A link is recomputed when any of these holds:

```
row is missing
OR matcher_version <> MATCHER_VERSION
OR clip_revision <> clippings.revision
OR status IN ('pending', 'failed')
```

`exact` and `repaired` rows are not recomputed on read, which is where the saving comes from. They
are invalidated explicitly by the events that can make them wrong:

| Event | Hook | Action |
| --- | --- | --- |
| Clipping changed or tombstoned on the device | `clippings._apply()` already writes a new `revision` | nothing to do, the `clip_revision` check catches it on the next read |
| OCR correction saved | `api.update_screenshot()` when `ocr_corrected` is present | `clip_match.invalidate_book(slug)` sets `status='pending'`, `matcher_version=0` for that book's links |
| Alias retitled | `api.set_alias()` | `clip_match.invalidate_document(hash)` |
| New screenshots archived | `archiver.run_sync()` after the batch | `clip_match.invalidate_book(slug)` for each touched book |
| Algorithm changed | bump `MATCHER_VERSION` | the version check catches every row |
| Anything else | `POST /api/clippings/relink` with `{"force": true}` | recompute everything |

**Idempotency.** The matcher is a pure function of the clipping fields plus the indexed screenshot
set, and the write is a single upsert on `clipping_id`. Recomputing the same inputs writes the same
row apart from `matched_at`. `_decorate()` builds a `_BookIndex` only for books that actually have a
stale row, so a page of fully-resolved clippings does zero index building.

**The device-facing path never touches this table.** `sync_router` (`GET` and `PUT /api/v1/clippings/{document}`)
does not call `_decorate()` and must not start. Only the UI routes (`ui_router`: `/api/clippings`,
`/api/books/{slug}/clippings`) read and write links.

#### API

`GET /api/clippings` and `GET /api/books/{slug}/clippings` keep their shapes and gain one field per
item:

```json
{
  "id": 88,
  "clip_id": "c-01HX...",
  "text": "Now, because Bigland did not get home...",
  "screenshot_id": 141,
  "link_status": "exact"
}
```

`screenshot_id` keeps the exact semantics it has today (the id the screenshots API serves, which is
`MIN(id)` of a stitched group, or `null`), so
[web/src/lib/components/ClippingCard.svelte](web/src/lib/components/ClippingCard.svelte) needs no
change to keep working.

```
POST /api/clippings/relink
body: { "book": "<slug>" | null, "force": false }
->
{ "checked": 412, "exact": 288, "repaired": 61, "failed": 40, "pending": 23, "duration_ms": 1180 }
```

`force: true` ignores the status and version checks and recomputes every link in scope.

#### Service changes

- [xteink_service/clip_match.py](xteink_service/clip_match.py):
  - Add `MATCHER_VERSION`, `init_db()`, `invalidate_book(slug)`, `invalidate_document(hash)`,
    `relink(book=None, force=False) -> dict`.
  - `_BookIndex.match()` returns `(id | None, status)`.
  - `attach_screenshot_ids()` reads the link table first, computes only the stale subset, upserts,
    and sets both `screenshot_id` and `link_status` on each clip dict.
  - Rewrite the module docstring's final paragraph.
  - `_load_books()` selects on the new `book_slug` column from 2.3 instead of canonicalising every
    distinct title in Python.
- [xteink_service/clippings.py](xteink_service/clippings.py): `init_db()` also calls
  `clip_match.init_db()`. `_decorate()` is otherwise unchanged.
- [xteink_service/api.py](xteink_service/api.py): `update_screenshot()` and `set_alias()` call the
  invalidators; add the `POST /api/clippings/relink` route.
- [xteink_service/archiver.py](xteink_service/archiver.py): after `run_sync()` finishes a batch,
  invalidate the books it touched.

#### UI

- `web/src/lib/api.ts`: add `link_status` to `Clipping`; add `api.clippings.relink(body)`.
- `web/src/lib/components/ClippingCard.svelte`: the existing `screenshot` chip gains a `title`
  attribute distinguishing `exact` from `repaired`. Nothing else changes.
- `web/src/routes/clippings/+page.svelte`: a small "re-link" button that calls
  `api.clippings.relink({ book: null, force: false })` and reloads. One button, no new page.

#### Backfill and migration

1. Deploy. `init_db()` creates the empty table.
2. Nothing else is required: the first read of any clippings route computes and stores the links it
   needs, because a missing row is treated as stale.
3. `POST /api/clippings/relink` with `{"force": true}` is the explicit repair, and is what to run
   after bumping `MATCHER_VERSION` if waiting for lazy recomputation is not wanted.
4. Re-running is safe at any time: it is an upsert keyed on `clipping_id`.
5. Rolling back means dropping the table. `attach_screenshot_ids()` keeps working the old way if
   the table is missing, because the stale check treats "no row" the same as "table has nothing for
   this id". Guard the table read with `except sqlite3.OperationalError` so a missing table degrades
   to the current behaviour rather than 500ing.

#### Risks

- **Stale `exact` links.** The invalidation list above is the whole defence. If an event is missed,
  a clipping opens the wrong screenshot, which the module docstring correctly calls worse than no
  link. Mitigation: `failed` and `pending` always retry, the relink button is one click, and a test
  asserts each invalidation hook.
- **Stitched-group id shifts.** `_sole_image()` returns `MIN(id)` of a `vault_png_path` group. A
  re-sync that inserts a lower id into an existing group changes the canonical id. The archiver
  invalidation hook covers this, and it is the specific case the original docstring was worried
  about.
- **Write amplification on read.** A page of 1000 clippings with no links does 1000 upserts on first
  load. Do them in one transaction with `executemany`, and only once.

---

### 2.7 Deleting and editing reading data

#### Why

Nothing in the app can correct or remove a reading record. `GET /api/reading-log` in
[xteink_service/api.py](xteink_service/api.py) reads `progress_updates` out of `koreader.db` and
renders it, and the only writer is the device. A bad row is therefore permanent: a file Calibre
re-paginated that pushed 100 percent, an article whose title slugged into a book, a position pushed
from a borrowed device. Every table this plan adds inherits it, because `reading_sessions`,
`reading_daily_stats`, and `reading_attempts` are all pure functions of that log. One wrong
percentage shows up in the heatmap, the streak, the pace, the year projection, and the finished
count, and there is no way to reach in and fix it.

There is also no way to remove a book. `DELETE /api/tbr/{id}` (2.3) clears `on_list` and
deliberately does **not** delete the book row, because the book may have screenshots and history
attached. A test sync, a duplicate alias, or a borrowed file stays in `/books`, in the sidebar
index, and in every statistic forever.

Both operations are the highest-stakes writes the app could grow, because the "Data model decision"
section above marks `progress_updates` as the one table that is **not** rebuildable. That is why
every operation here is undoable, and why the undo is not optional polish on top but part of the
delete path itself.

#### Data model

Two pieces: undo payloads, which reuse Plan 1's table, and a new suppression table.

**Undo adds no table.** `PLAN-1-QUICK-WINS.md` item 1.3 owns the generic `undo_log` table in
`state.db`, the module `xteink_service/undo.py` with its reversal registry keyed on `kind`, the
`POST /api/undo` endpoint taking `{"token": "..."}`, and `GET /api/undo/recent`. This item registers
three kinds against that registry and nothing else:

```python
undo.register("progress_edit",   reverse=erase.undo_progress_edit,   ttl_days=30)
undo.register("progress_delete", reverse=erase.undo_progress_delete, ttl_days=30)
undo.register("book_delete",     reverse=erase.undo_book_delete,     ttl_days=30)
```

**Do not define a second undo table, a second endpoint, or a different token format.** Every
destructive endpoint below returns `"undo_token"` in its response and that token is Plan 1's.

**TTL is 30 days here, not Plan 1's default.** A misclicked clipping is noticed in the same minute,
so a short TTL is right for it. A delete of reading data is noticed on a completely different
schedule: a missing book is noticed when you go looking for it, and a wrong month of statistics is
noticed when the month ends. Thirty days is the first interval that covers "I checked my stats at
the end of the month and something is missing". The cost is bounded and measurable: the database
side is a few kilobytes of JSON per progress delete and tens of kilobytes for a book, and the vault
side is the deleted book's PNGs sitting in `.trash/<token>/` for thirty days. Plan 1's expiry sweep
is the purger; extend it so expiring a `book_delete` row also removes the matching
`VAULT_PATH/.trash/<token>/` directory. There is no scheduler in this service (2.3 says so
explicitly), so the sweep runs where Plan 1 already runs it, not on a timer added here.

**`GET /api/undo/recent` must not select the `payload` column.** A `book_delete` payload is the
largest blob this application will ever store in `state.db`, and the recent list only needs
`token`, `kind`, `label`, `created_at`, and `expires_at`.

The new table, in `state.db`:

```sql
CREATE TABLE IF NOT EXISTS sync_suppressions (
    document    TEXT NOT NULL,
    scope       TEXT NOT NULL,        -- progress | clippings | screenshots
    mode        TEXT NOT NULL,        -- ignore_before | ignore_all
    since       INTEGER NOT NULL DEFAULT 0,
    reason      TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (document, scope)
);
```

Column notes:

- The primary key is the whole index. Every consulting path looks up one `(document, scope)` pair,
  so there is no second index to add and no scan anywhere.
- `document` holds a kosync digest for `scope IN ('progress', 'clippings')`, because those are the
  two device-facing paths and the digest is the only key they have. For `scope = 'screenshots'` it
  holds a **book slug** instead, because the archiver has no digest at dedup time: it has a book
  title parsed out of the screenshot filename on the device
  ([xteink_service/archiver.py](xteink_service/archiver.py) `parse_screenshot_filename`). That is a
  deliberate wart. The alternative is a second table with the same three columns and a different key
  name, which is worse for a row count that will stay in the low dozens. The column comment in
  `init_db()` must say so.
- `since` is polymorphic by scope, which is the other wart, and it is listed in full in the
  suppression section below.
- `reason` is free text shown in the UI (`"book deleted 2026-11-08"`), never parsed.

**No `deleted_at` column is added to `progress_updates`.** That table is the device-facing hot path:
`put_progress` writes it on every push and `_latest`, `_latest_n`, `latest_across`, and
`devices_for_document` all read it without one. Adding a soft-delete column means adding a predicate
to every one of those reads and to the kosync response path, for a table whose whole job is to stay
boring. The recoverable copy lives in `undo_log.payload` in `state.db` instead, which is consistent
with the "all new state lives in `state.db`" decision this plan already made. `koreader.db` keeps
exactly the two new indexes 2.1 adds and nothing else.

**Restoring the original `id` is safe.** `progress_updates.id` is `INTEGER PRIMARY KEY
AUTOINCREMENT`, so SQLite records the high-water mark in `sqlite_sequence` and never hands a deleted
id out again. The undo re-inserts each row with its original `id`, which is what makes 2.1's
`ORDER BY timestamp, id` reproduce the exact same session boundaries after a restore. A re-insert
that let SQLite pick a fresh id would silently reorder same-second ties.

Payload shape for `progress_delete`:

```json
{
  "kind": "progress_delete",
  "rows": [
    {"id": 4821, "document": "a1b2c3...", "progress": "/body/DocFragment[12]/body/div/p[7]",
     "percentage": 0.465, "device": "CrossPoint", "device_id": "crosspoint-reader",
     "title": "", "author": "", "extra": null, "timestamp": 1793206500}
  ],
  "suppressions": [
    {"document": "a1b2c3...", "scope": "progress", "mode": "ignore_before",
     "since": 1793206500, "previous": null}
  ],
  "rederive": {
    "pairs": [["a1b2c3...", "crosspoint-reader", 1793206500]],
    "slugs": ["the-hidden-keys"]
  }
}
```

`rows` carries **every** column of `progress_updates`, not a projection. `previous` holds the
suppression row this delete overwrote, or `null`, so undo restores the prior suppression rather than
clearing one that was already there. `rederive.pairs` is `(document, device_key, t_min)` and
`rederive.slugs` is the set of books whose attempts and status need re-deriving.

`progress_edit` has the same shape with a single row holding the values **before** the edit.
`book_delete` carries one array per cascaded table, the clipping tombstone records, the citation
state records described below, the vault move list, and the same `suppressions` and `rederive`
blocks.

**What a book delete does to each table:**

| Table | Action | Why |
| --- | --- | --- |
| `books` | soft delete, `deleted_at = now` | 2.3's `fold_tbr()` guards on `WHERE deleted_at IS NULL` precisely so a re-run cannot resurrect a deleted book. Removing the row would break that guard. |
| `reading_attempts` | soft delete (2.3 already has the column) | Manual attempts are source data; they go in the payload anyway so undo does not depend on the column surviving a later schema change. |
| `reading_sessions` | hard delete | Pure derived, rebuildable from `progress_updates`. No `deleted_at` column and none is added. |
| `reading_daily_stats` | **recomputed, not deleted** | A day can hold sessions for other books. The affected dates go through `recompute_day()` like any other change. |
| `progress_updates` | hard delete, full rows serialised | Constraint above: no soft-delete column on this table. |
| `synced_screenshots` | hard delete, full rows serialised | The row **is** the dedup record (`UNIQUE(device_path, content_hash)`), so it cannot be kept and still count as deleted. See the suppression section. |
| `highlights` | hard delete, full rows serialised | They join to `synced_screenshots` for a title and an image. A soft-deleted highlight whose parent screenshot row is gone cannot render in `/trash` at all, so soft-deleting it would only produce a broken row. |
| `clippings` | **tombstone, never delete** | Constraint below, and Plan 1 item 1.3 already owns the tombstone plus `trashed_text`. Reuse `clippings.delete_clipping()`; do not add a second delete path. |
| `clipping_screenshot_links` | hard delete | Pure derived (2.6), recomputed on demand, and the matching clipping rows survive as tombstones. |
| `writing_entries` | soft delete (2.5 already has the column) | Source data, and it belongs in `/trash` with the rest. Only entries whose `book_slug` is the deleted book are touched. The per-entry `remove_writing_entry()` vault edit is **not** called, because the whole note is moving to `.trash/` anyway. |
| `writing_refs` | **`source_state = 'deleted'`, never removed** | Constraint below. No row is deleted and no `snapshot` is touched. |
| `document_aliases` | hard delete, rows serialised | Leaving the alias means the device's next push resolves a title, `books.touch()` re-creates the book row, and the delete silently undoes itself. |
| `book_pages` | hard delete, row serialised | Keyed on title. Serialised rather than re-fetched so undo needs no network call. |
| `search_docs` / `search_index` | `search_index.unindex()` per removed ref, if Plan 1 item 1.1 has landed | Guarded with `except sqlite3.OperationalError`, same as everything else here. |
| vault files | moved to `.trash/<token>/` | See "Vault files" below. |
| `sync_suppressions` | three rows written | See below. |

#### A delete must not edit writing

This is why the ordering table makes 2.5 a hard dependency of 2.7. **A delete that is unaware of
citations silently edits writing**, and that is the one outcome a commonplace tool cannot have.

The rule is narrow and absolute: a book delete may change a citation's state, and may not change
its content. For every `writing_refs` row pointing at a clipping, screenshot, or highlight this
delete removed or tombstoned, set `source_state = 'deleted'`. Remove no ref row, and overwrite no
`snapshot`. The entry keeps rendering from its own snapshots with the quiet "source removed"
affordance 2.5 describes, and nothing cascades into the prose.

This matters in two separate cases, not one:

1. **Entries belonging to the deleted book** are soft-deleted with it, so they land in `/trash`
   along with everything else. Their bodies and their refs survive untouched, which is what makes a
   restore bring back readable writing rather than a page of empty blockquotes.
2. **Entries belonging to a different book** may still cite this one. Nothing in the schema
   constrains a ref to the entry's own book, and quoting one book inside writing about another is
   the normal case for a commonplace book. Those entries are not deleted, not edited, and not
   hidden. Only their `source_state` changes.

The `book_delete` payload carries one `{"ref_id": ..., "previous": "live"}` record per touched ref,
so undo restores the prior state rather than blanket-setting `live` on rows that were already
`deleted` or `drifted` before this operation ran.

#### Suppression and the resurrection problem

This is the crux of the item. **The device holds its own state, and everything deleted here comes
back unless something stops it.**

Three separate resurrection paths, all real:

1. **Progress.** Delete a book's `progress_updates` rows and the X4's next kosync push re-creates
   one. `put_progress` has no notion of "this book was removed"; it stores whatever arrives. The
   book is back in `/books` within one sync interval, at the position the device still holds.
2. **Clippings.** The device re-uploads any clipping id it still holds. Plan 1 item 1.3 already
   established that a clipping row is never deleted for exactly this reason, and that a tombstone is
   what makes the device drop the id on its next pass. Reuse the tombstone. There is a one-cycle
   hole in it, described below.
3. **Screenshots.** The archiver's dedup check is `is_path_synced(filepath)` then
   `is_synced(filepath, content_hash)` against `synced_screenshots`, whose key is
   `UNIQUE(device_path, content_hash)`. **Removing those rows is precisely what makes the next File
   Transfer re-download, re-OCR, and re-archive the same images.** The dedup record and the archive
   record are the same row, so a delete cannot keep one without keeping the other.

The answer is a server-side filter, modelled on BookOrbit's `koreader_progress_resets`
(`bookorbit/server/src/db/schema/koreader.ts`), which exists for the same reason: a wiped position
has to survive the next push from a device that still holds the old one. BookOrbit writes a marker
per `(user, book file)` and consults it on the sync path. `sync_suppressions` is the same idea with
a scope column, because this service has three device-facing paths rather than one.

**What `since` means per scope, and where each is consulted:**

| `scope` | `since` holds | Consulted in | Retired by |
| --- | --- | --- | --- |
| `progress` | a `progress_updates.timestamp` bound | [xteink_service/koreader_sync.py](xteink_service/koreader_sync.py) `put_progress`, and the derivation reader in `reading.py` | undo, or the user clearing it in the UI |
| `clippings` | the highest tombstone `revision` the delete wrote | [xteink_service/clippings.py](xteink_service/clippings.py) `_apply()` | automatically, when the device's `GET` cursor for that document reaches `since` |
| `screenshots` | unused, always `0` | [xteink_service/archiver.py](xteink_service/archiver.py) `run_sync()` | undo, or the user clearing it in the UI |

**Progress scope.** The check goes in `put_progress`, **after** `_store.record_device()` and the
existing unchanged-position early return, and **before** `_store.upsert()`:

```python
sup = suppressions.get(update.document, "progress")   # one primary-key read
if sup and (sup["mode"] == "ignore_all" or ts <= sup["since"]):
    logger.info("Suppressed push for %s from %s (%s)", update.document[:12],
                update.device or "unknown device", sup["mode"])
    return _kosync_view(_store._latest(update.document))
```

A suppressed device is still registered, because `record_device()` runs first: the device is known,
it is just not writing history. The handler still returns a valid kosync body. Returning an error
instead would make the device retry the same push forever, and `_kosync_view(None)` already returns
`{}`, which is the honest answer when every row for that document is gone.

`ignore_all` is the only mode that stops a **live** device. `ignore_before` cannot, and the plan
should not pretend otherwise: `progress_updates.timestamp` is server-assigned
(`ProgressStore.upsert` passes `int(datetime.now(timezone.utc).timestamp())`), so a push arriving
now is always newer than any `since` written in the past. `ignore_before` exists for the **replay**
cases, which are the ones that actually carry a historical timestamp:

- `koreader.db` is restored from a backup taken before the delete.
- A row is re-inserted by hand or by an import.
- The derivation reader in `reading.py` rescans `progress_updates` during
  `POST /api/sessions/rebuild`.

That last one is why the filter also belongs in the derivation reader and not only on the hot path:
a restored `koreader.db` still must not put the deleted rows back into `reading_sessions` and the
rollup. One extra predicate on a query that already scans one document's history is free.

**When each mode is written, and the rule that keeps `ignore_before` correct:**

`ignore_before` is written **only when the delete removed every row for that document at or before
`since`.** That is true for an unbounded-`from` range delete and for a single-entry delete of the
oldest row, and it is false for a delete in the middle of a history. A middle delete writes **no**
progress suppression at all, and the response says so:

```json
{"suppression": null, "suppression_reason": "partial range, a restore would bring these rows back"}
```

That restriction is what makes `ON CONFLICT(document, scope) DO UPDATE SET since = MAX(since,
excluded.since)` safe. Without it, deleting a row at `T1` and later a row at `T2 > T1` would leave
`since = T2`, which on a restore would suppress every row between them that the user kept. With it,
both writes assert "everything at or before my `since` is gone", so the maximum is still fully
covered. `ignore_all` always wins over `ignore_before` and is never downgraded.

**Clippings scope.** Tombstones do the work; the suppression closes a one-cycle hole in them. The
device's sync order per book is: `PUT` its pending items, then `GET` the pages back down
(`CLAUDE.md`, "Clipping sync requires Server Type not KOSync"). If the reader was offline when the
book was deleted, its `PUT` lands first and `_apply()`'s live branch clears the tombstone before the
device ever sees it. One cycle later the user's delete has silently reverted.

So: a `clippings` suppression with `mode = 'ignore_before'` and `since` set to the highest tombstone
revision the delete wrote causes `_apply()` to drop non-tombstone items for that document. The check
is one primary-key read **per request**, not per item, because a `PUT /api/v1/clippings/{document}`
is always scoped to one document, and it goes inside the `BEGIN IMMEDIATE` block
`upload_clippings()` already opens.

Two hard rules on that path:

- **Tombstones are never suppressed.** An item with `deleted = 1` is always applied. Suppressing a
  delete is the one thing that can never be the user's intent.
- **The response must still be `{"sync_version": 2, "accepted": 1}`** even when every item in the
  request was dropped. The firmware aborts its whole sync unless it sees `accepted: 1`, and an
  aborted sync means the device also never paginates the tombstones down, which is the opposite of
  what the suppression is trying to achieve.

The retirement signal is the pull. `list_clippings()` already computes the cursor it returns; when
that cursor reaches `since` for a suppressed document, the device has the tombstones and the
suppression deletes itself. This mirrors BookOrbit's retirement-on-convergence rule
(`koreader_progress_reset_devices`), and it matters for the same reason: a marker kept past its
purpose keeps filtering uploads from a device that has already complied.

**Screenshots scope.** `run_sync()` runs one query per sync, not per file:

```sql
SELECT document FROM sync_suppressions WHERE scope = 'screenshots';
```

The result is a set of book slugs held for the duration of the run. A group whose
`canonical_book_slug(book)` is in the set is skipped before `_download_file()`, so a suppressed book
costs one set membership test and zero network. Only `ignore_all` is meaningful here: the dedup key
has no time component, so there is nothing for `ignore_before` to compare.

**Hot path cost.** `put_progress` already opens `state.db` twice per push, once in
`_record_client_title()` and once in `_write_progress_to_vault()`. The suppression lookup adds a
third connection doing a single two-column primary-key read, and it sits ahead of
`_maintain_reading_data()` so it is counted against the same budget described in "Hot path budget"
below. A suppressed push is **cheaper** than a normal one, because it returns before `upsert()`,
before the vault write, and before any derivation. No new environment variable is added; 2.1's
`READING_DERIVE` stays the only one in this plan.

**Degradation.** Every suppression read is wrapped in `except sqlite3.OperationalError` returning
"no suppression", exactly as 2.6 requires for `clipping_screenshot_links`. A missing
`sync_suppressions` table must never fail a device push.

**What this cannot do, stated plainly.** A suppression is a server-side filter, not a device-side
delete. The X4 still holds the book, still holds its position, and still holds its clippings. If the
reader opens the book again, it keeps reading from where it was, and the server simply refuses to
record it. There is no protocol affordance for telling a kosync client to forget a document, and
inventing one is firmware work this service cannot do. The honest framing for the UI copy is
"stop recording this book", not "remove it from your reader". The one case that resolves itself is
clippings, because the tombstone protocol does have a forget instruction and the device honours it.

#### Re-derivation

Editing or deleting progress invalidates `reading_sessions`, then `reading_daily_stats`, then
`reading_attempts` and the auto status on `books`. All three already have a correct narrow path in
this plan, and 2.7 **reuses them rather than writing a second window calculator**.

2.1 defines `extend_for_update(document, device_key, t)`, whose window is anchored on

```
anchor = SELECT MAX(timestamp) FROM progress_updates
          WHERE document = ? AND device_key_expr = ?
            AND timestamp <= (t - SESSION_GAP_S)
```

and which then deletes every session with `ended_at > anchor` and re-derives from the surviving
rows. For a change that touched one or more rows, pass the **earliest affected timestamp** as `t`:

```python
def _rederive(pairs: dict[tuple[str, str], int], slugs: set[str]) -> list[str]:
    """pairs maps (document, device_key) to the earliest affected timestamp."""
    days: set[str] = set()
    for (document, device_key), t_min in pairs.items():
        days |= reading.extend_for_update(document, device_key, t_min)
    reading.recompute_days(days)
    for slug in slugs:
        books.rebuild(book=slug)
    return sorted(days)
```

**Call it after the delete commits, never before.** `extend_for_update` computes its anchor from
`progress_updates`, so running it first would anchor on a row that is about to disappear. Running it
after means the anchor is always a surviving row.

Why this window is the narrowest correct one, and why a full rebuild is not needed:

- Sessions are grouped per `(document, device_key)` (2.1). No row belonging to any other pair can
  change this pair's grouping, so no other pair needs recomputing.
- Within the pair, `anchor` is by construction separated from every changed row by more than
  `SESSION_GAP_S`, so no boundary at or before `anchor` can move. Deleting rows can only widen a
  gap, which can split one session into two but can never merge two across the anchor. Editing a
  percentage changes no boundary at all. Editing a timestamp is modelled as a delete at the old
  value plus an insert at the new one, so `t_min = min(old_ts, new_ts)` and the single window covers
  both.
- Moving a row to a different `document` is two pairs, each with its own `t_min`, and both go in the
  `pairs` dict.

`extend_for_update` returns the set of `local_date` values it touched. 2.2 already specifies that
both the delete and the insert contribute their dates, so a session that moved across midnight puts
both dates in the set, and a day emptied by a delete is handled by `recompute_day()`'s
`DELETE` followed by the `HAVING COUNT(*) > 0` guard, which correctly leaves no row behind.

`books.rebuild(book=slug)` re-derives attempts and auto status for the one book. 2.3 specifies that
it deletes only `source = 'auto'` attempts and never touches a `status_source = 'manual'` row, so a
re-derivation after a delete cannot overwrite a human's status or a hand-entered attempt.

**Undo re-derives too, through the same function.** The reversal for each kind re-inserts its rows,
removes the suppressions it wrote, and then calls `_rederive()` with the `rederive` block stored in
the payload. No caller has to remember: the registry entry owns the whole sequence, which is the
point of keying reversals on `kind`.

#### API

```
PUT /api/progress/{id}
body: { "percentage"?: 0.465, "timestamp"?: 1793206500, "document"?: "a1b2c3..." }
->
{
  "updated": 4821,
  "document": "a1b2c3...",
  "moved": false,
  "sessions_rederived": 3,
  "days_recomputed": ["2026-11-03"],
  "undo_token": "u_01HX7Q8M4"
}
```

- `percentage` is a fraction from 0.0 to 1.0, matching the column and `progress_updates.percentage`.
  Anything outside that range is a 400.
- `timestamp` is a unix integer. Changing it can move the entry to a different `local_date` and can
  split or merge a session; both dates appear in `days_recomputed`.
- `progress` is **not** editable. It is the device's own position token (a CFI-like string that
  `vault_writer._parse_cfi` only ever reads), and the server has no way to construct a valid one for
  a percentage it was handed. Sending it is a 400 naming the reason.
- `document` moves the entry to a different book. It returns 409 when the target hash has no
  `document_aliases` row, so a typo cannot create an orphan. **The usual fix for "wrong book" is
  `PUT /api/aliases/{hash}`**, which retitles every row for that hash at once and already calls
  `reading.relabel_document()` (2.1). Per-entry moves exist for the case where one genuine push
  landed under the wrong hash, and the endpoint description says so.

```
DELETE /api/progress/{id}
->
{
  "deleted": 4821,
  "document": "a1b2c3...",
  "suppression": {"scope": "progress", "mode": "ignore_before", "since": 1793206500},
  "sessions_rederived": 2,
  "days_recomputed": ["2026-11-03"],
  "undo_token": "u_01HX7Q8M4"
}
```

```
DELETE /api/books/{slug}/progress?from=YYYY-MM-DD&to=YYYY-MM-DD
->
{
  "book_slug": "the-hidden-keys",
  "documents": 2,
  "deleted": 184,
  "from": "2026-10-01",
  "to": "2026-10-31",
  "suppressions": [
    {"document": "a1b2c3...", "scope": "progress", "mode": "ignore_before", "since": 1795600000}
  ],
  "sessions_rederived": 31,
  "days_recomputed": ["2026-10-01", "..."],
  "attempts_rederived": 1,
  "undo_token": "u_01HX7Q8M5"
}
```

`from` and `to` are inclusive local dates converted to a unix range with the container `TZ`, the
same conversion `local_date` uses in 2.1, so the bounds mean what the reading log shows. The slug is
expanded to every document hash that resolves to it, the same way `api._hashes_for_book()` already
does. With both bounds absent the endpoint deletes every progress row for the book and writes
`ignore_all` per document. With either bound present it writes `ignore_before` only when the rule
above holds, and `"suppression": null` with a reason otherwise.

```
DELETE /api/books/{slug}?dry_run=1
DELETE /api/books/{slug}?confirm=1
```

Per `PLAN-3-POLISH.md` item 3.7, the delete is preview-then-apply. `dry_run=1` returns 200 and
writes nothing; `confirm=1` applies. Without either, the endpoint returns 400 with
`{"detail": "Pass dry_run=1 to preview or confirm=1 to apply"}`, which is the same second gate Plan
1 item 1.3 puts on its purge routes.

Two deliberate differences from 3.7, both narrow:

- **Query parameters, not a request body.** 3.7's dry run flag rides in a `POST` body. A `DELETE`
  with a body is not reliably forwarded by every proxy, and this service sits behind NPM. The flag
  and the diff-plan concept are 3.7's; only the transport differs.
- **The apply leg is synchronous, not a 3.3 job.** 3.7's apply leg is a job because a vault rebuild
  rewrites every note. A book delete touches a few thousand rows and performs one directory rename,
  which is milliseconds. That is why 2.7 depends on 3.7 for the preview leg only.

Dry run response:

```json
{
  "dry_run": true,
  "book_slug": "the-hidden-keys",
  "title": "The Hidden Keys",
  "documents": ["a1b2c3...", "d4e5f6..."],
  "counts": {
    "books": 1,
    "reading_attempts": 2,
    "reading_sessions": 41,
    "progress_updates": 184,
    "synced_screenshots": 25,
    "highlights": 6,
    "clippings": 12,
    "clipping_screenshot_links": 12,
    "writing_entries": 3,
    "document_aliases": 2,
    "book_pages": 1
  },
  "daily_stats_recomputed": 29,
  "clippings_tombstoned": 12,
  "writing_refs_marked_deleted": 11,
  "vault_paths": [
    "Books/The Hidden Keys.md",
    "Books/The Hidden Keys/2026-07-04-01.png",
    "Books/The Hidden Keys/2026-07-04-01.json"
  ],
  "vault_truncated": false,
  "reading_log_lines": 29,
  "suppressions": [
    {"document": "a1b2c3...",       "scope": "progress",    "mode": "ignore_all"},
    {"document": "a1b2c3...",       "scope": "clippings",   "mode": "ignore_before"},
    {"document": "the-hidden-keys", "scope": "screenshots", "mode": "ignore_all"}
  ],
  "warnings": [
    "29 reading_daily_stats rows also cover other books and will be recomputed, not deleted",
    "29 lines in Reading Log/*.md and Reading Log.md name this book and are left in place",
    "11 citations in 4 writing entries quote this book, keep their quoted text, and are marked source removed",
    "1 of those entries is about another book and is not deleted"
  ]
}
```

`vault_paths` is capped at 200 entries with `vault_truncated: true`, matching 3.7's cap on
`changes`. `daily_stats_recomputed` is reported separately from `counts` precisely because those
rows are not deleted, and `writing_refs_marked_deleted` is reported separately for the same reason:
no ref row is removed. `counts.writing_entries` covers only the entries about **this** book, which
are the only ones a delete may touch. The confirm dialog renders `counts` prominently and `warnings` underneath, and
its button reads "Delete 184 progress entries and 25 screenshots", never "OK", following 3.7.

Apply response:

```json
{
  "dry_run": false,
  "book_slug": "the-hidden-keys",
  "deleted": { "...the same counts object..." },
  "clippings_tombstoned": 12,
  "writing_refs_marked_deleted": 11,
  "vault_moved_to": ".trash/u_01HX7Q8M6/",
  "vault_files_moved": 51,
  "days_recomputed": ["2026-07-04", "..."],
  "undo_token": "u_01HX7Q8M6",
  "undo_expires_at": 1797926400
}
```

Undo is Plan 1's endpoint, unchanged:

```
POST /api/undo
body: { "token": "u_01HX7Q8M6" }
->
{
  "token": "u_01HX7Q8M6",
  "kind": "book_delete",
  "restored": { "...the same counts object..." },
  "vault_files_restored": 51,
  "suppressions_removed": 3,
  "clippings_restored": 12,
  "days_recomputed": ["2026-07-04", "..."]
}
```

Replaying a token that has already been applied returns 409 with
`{"detail": "Already undone"}`, which is Plan 1's contract. None of these routes may be added to
`_PUBLIC_ALLOWED`; that tuple stays `("/syncs", "/users", "/health", "/api/v1/clippings")`.

#### Vault files

A book delete removes `Books/<Title>.md` and everything under `Books/<Title>/`. **Move them, never
unlink them.**

Destination is `VAULT_PATH/.trash/<token>/`, with the vault-relative path preserved underneath, so
undo is a plain reverse rename with no path reconstruction:

```
Books/The Hidden Keys.md                  ->  .trash/u_01HX7Q8M6/Books/The Hidden Keys.md
Books/The Hidden Keys/2026-07-04-01.png   ->  .trash/u_01HX7Q8M6/Books/The Hidden Keys/2026-07-04-01.png
```

The root comes from the row, not from a constant: articles live under `Articles/` via
`content_kind.vault_root_for()`, and `api._vault_root_of()` already resolves it per row. Reuse that.

`VAULT_PATH` is a single bind mount (`/srv/docker/data/syncthing/shares/xteink-vault` in
[docker-compose.yml](docker-compose.yml)), so `.trash/` is on the same filesystem and the move is an
atomic `Path.rename`. On `OSError` fall back to copy-then-unlink and record it in the response; if
even that fails, abort before touching the database.

**Order of operations, and why it is this order:**

1. Write the `undo_log` row with the full payload and commit it.
2. Move the vault files into `.trash/<token>/`.
3. Delete and tombstone the database rows and write the suppressions, in one transaction.
4. Re-derive.

Every failure point leaves a recoverable state. A failure at step 2 has deleted nothing and leaves
an orphan undo row that the TTL sweep removes. A failure at step 3 leaves the files in
`.trash/<token>/` and the token still works, so `POST /api/undo` puts them back. Doing the database
work first would mean a failure at the file step left rows deleted and the note still present, which
is the one combination with no single repair.

There is a second copy regardless: `VaultWriter.write_screenshot_meta()` writes a JSON sidecar next
to every PNG carrying `device_path`, `content_hash`, `book_title`, `sync_date`, and `ocr_text`. Those
sidecars move into `.trash/` with the images, so even a lost `undo_log` row leaves the screenshot
archive reconstructable by hand. That is the same "the vault folder is self-contained" property
`CLAUDE.md` already claims, and the move preserves it.

**Does `.trash/` belong in `.stignore`? Yes, and it is already there.**
[test_scripts/init-vault.sh](test_scripts/init-vault.sh) writes `.trash` into the vault's `.stignore`
today, and [README.md](README.md) documents it. Three reasons it stays:

1. A trash directory is server-local recovery state, not vault content. Syncing it would push every
   deleted PNG to every peer and into Obsidian's file list, which is the opposite of what a delete
   is for.
2. The entry already exists for Obsidian's own `.trash`, so reusing the same directory name costs
   nothing and changes no existing setup. A new ignore pattern would have to be added to every
   already-paired peer by hand.
3. It is the only way to keep the delete meaningful on a phone. A peer that received `.trash/`
   would show the deleted book's images in Obsidian's search.

**Correct the premise while here.** Because `.trash` is ignored, peers see a plain delete of
`Books/<Title>.md`; they do not see a rename. Syncthing would not generally propagate an arbitrary
move as a rename anyway: it reuses local blocks so the peer does not re-download, but the remote
still observes a delete plus a create. **The move-not-unlink argument stands on server-side
recoverability, not on peer-side rename semantics**, and the plan should not claim otherwise. Peer
protection is Syncthing's own file versioning, which is already what protects a peer from any
deletion this service performs today.

One inconsistency worth reconciling while touching this: `init-vault.sh` writes a bare `.trash`
while `README.md` shows `(?d).trash`. The `(?d)` prefix marks a pattern as deletable, which lets
Syncthing remove the matched files when it needs to clear the containing directory. The service's
trash must **not** be deletable, so the bare form in the script is the correct one and the README
line is the one that is wrong.

#### Service changes

Two new modules, kept apart on purpose.

New file `xteink_service/suppressions.py`. Deliberately tiny, importing only `os` and `sqlite3`, so
the kosync hot path can import it without dragging in the delete machinery:

- `_state_db()` / `_connect()` - env read inside the function, never at import.
- `init_db()` - the `CREATE TABLE IF NOT EXISTS` above. Idempotent.
- `get(document, scope) -> dict | None` - one primary-key read, returns `None` on
  `sqlite3.OperationalError`.
- `active(scope) -> set[str]` - the one-query-per-run form the archiver uses.
- `put(document, scope, mode, since, reason) -> dict | None` - the upsert with the `MAX(since)` rule,
  returning the row it replaced so the caller can store it in the undo payload.
- `clear(document, scope) -> bool`, `restore(row) -> None`.

New file `xteink_service/erase.py`:

- `init_db()` - calls `suppressions.init_db()` and registers the three undo kinds.
- `edit_progress(progress_id, *, percentage=None, timestamp=None, document=None) -> dict`.
- `delete_progress(progress_id) -> dict`.
- `delete_book_progress(slug, *, since=None, until=None) -> dict`.
- `plan_book_delete(slug) -> dict` - the dry run. Opens no file for writing and creates no
  directory, which is 3.7's assertable property.
- `delete_book(slug) -> dict` - the four-step order above.
- `undo_progress_edit(payload)`, `undo_progress_delete(payload)`, `undo_book_delete(payload)`.
- `_rederive(pairs, slugs) -> list[str]` - the function in the re-derivation section.

Modified:

- [xteink_service/koreader_sync.py](xteink_service/koreader_sync.py) `put_progress()`: the
  suppression check, placed after the unchanged-position early return and before `_store.upsert()`.
- [xteink_service/clippings.py](xteink_service/clippings.py): `init_db()` also calls
  `suppressions.init_db()`; `upload_clippings()` reads the suppression once per request and passes
  it to `_apply()`, which drops non-tombstone items and always applies tombstones;
  `list_clippings()` retires the suppression when the returned cursor reaches `since`.
- [xteink_service/archiver.py](xteink_service/archiver.py) `run_sync()`: one
  `suppressions.active("screenshots")` call before the group loop, and a skip on slug membership
  before `_download_file()`.
- `xteink_service/reading.py` (2.1): the derivation reader excludes rows covered by a `progress`
  suppression, so a restored `koreader.db` does not re-materialise deleted history.
- [xteink_service/vault_writer.py](xteink_service/vault_writer.py): new
  `trash_paths(paths, token) -> list[tuple[str, str]]` and `restore_paths(moves) -> int`. Both take
  vault-relative paths and never compute a book directory themselves, so the caller owns the root
  choice and the two are exact inverses.
- [xteink_service/api.py](xteink_service/api.py): the four routes, each thin, each calling into
  `erase.py`. `set_alias()` already calls `reading.relabel_document()` (2.1) and needs no change.

#### UI

- `web/src/lib/api.ts`: add `api.progress.update(id, body)`, `api.progress.remove(id)`,
  `api.books.deleteProgress(slug, params)`, and `api.books.remove(slug, { dry_run | confirm })`.
  Add `BookDeletePlan` and `ProgressEdit` types. **`api.undo.*` is Plan 1 item 1.3's namespace:
  extend it, do not add a second one.**
- [web/src/routes/log/+page.svelte](web/src/routes/log/+page.svelte): each `ProgressEntry` inside
  the existing `{#each grouped as group}` list gains an edit and a delete affordance, revealed on
  hover and always present on touch. Edit opens a small inline form over the row with percentage and
  timestamp fields. The book field is **not** a free-text hash input: it is a link to `/aliases` for
  that hash, because retitling the alias is the right fix for "wrong book" and the per-entry move is
  the rare case.
- New `web/src/lib/components/ProgressEntryEditor.svelte`: the inline form, emitting save and cancel.
- [web/src/routes/books/[book]/+page.svelte](web/src/routes/books/%5Bbook%5D/+page.svelte): a
  "Danger zone" block at the bottom of the page, collapsed by default, with two controls. "Delete
  reading progress" takes optional date bounds. "Delete this book" loads the dry run and opens
  `ConfirmApplyDialog.svelte` from Plan 3 item 3.7 with the plan. After a successful delete,
  `goto('/books', { replaceState: true })` so the deleted book's route cannot 404 on the back button.
- The shared `UndoToast.svelte` from Plan 1 item 1.3 is already mounted once in
  `web/src/routes/+layout.svelte`. Every call here hands it the `undo_token` from the response.
  **Do not add a second toast, a second store, or a per-page undo button.**
- Copy rules, which matter more than usual here: the book delete dialog says the reader still holds
  the book and will not be changed; the progress delete says recording stops rather than that the
  device is cleared; and the suppression chip on the book page reads "not recording" with a control
  to clear it.

#### Backfill and migration

1. Deploy. `suppressions.init_db()` creates the empty table on first touch, called from
   `clippings.init_db()`, from `erase.init_db()`, and from the kosync helper.
2. **Nothing to backfill.** An empty `sync_suppressions` means every lookup misses and every path
   behaves exactly as it does today. There is no historical delete to reconstruct, because no delete
   was possible before this item.
3. Re-running is a no-op in both directions: `init_db()` is `CREATE TABLE IF NOT EXISTS` and every
   write is an upsert on `(document, scope)`.
4. Rolling back means dropping `sync_suppressions` and removing the three registry entries. Every
   read is guarded with `except sqlite3.OperationalError` returning "no suppression", so a dropped
   table degrades to today's behaviour rather than failing a device push, matching 2.6's rule for
   `clipping_screenshot_links`.
5. `undo_log` rows written by Plan 1 are untouched. A rollback that leaves `book_delete` tokens
   behind must fail loudly rather than silently doing nothing: Plan 1's registry returns 409 for a
   `kind` it cannot reverse, and this item relies on that.

#### Risks

- **The device is not under our control.** The whole suppression mechanism is a filter on what the
  server records, and the X4 still holds the book, the position, and the clippings. Clippings are
  the one exception, because the tombstone protocol does have a forget instruction. Everything else
  is mitigated by naming it honestly in the UI copy, not by a cleverer server.
- **`ignore_all` is sticky and invisible if it is not surfaced.** A user who deletes a book's
  progress and later wants to read it again will push positions into a server that silently drops
  them. The book page must show the suppression and offer to clear it, and the suppressed-push log
  line above is what makes it diagnosable from `docker compose logs`.
- **A middle-of-history delete writes no suppression.** A `koreader.db` restore brings those rows
  back. This is deliberate: over-suppressing would discard rows the user kept, which is worse. The
  "Data model decision" section already tells operators to run the three rebuild endpoints after a
  mismatched restore, and this is one more reason to.
- **`canonical_book_slug` collisions make a book delete over-delete.** 2.3 already names this risk
  for the `books` table; a delete makes it destructive rather than merely confusing. Mitigation: the
  dry run lists every contributing document hash and the vault paths, so a collision is visible in
  the confirm dialog before anything is removed, and the undo token covers it if it is not.
- **Shared vault files keep their lines.** `Reading Log/YYYY-MM-DD.md` and `Reading Log.md` contain
  one line per book per day, and those lines stay. Removing them would mean surgically editing files
  that belong to other books, which is a 3.7-shaped diff operation and is not done here.
  `POST /api/vault/rebuild` does not remove them either, for the same reason Plan 1 item 1.3 gives
  for orphaned `==` marks. The dry run reports the count so the user is not surprised.
- **Thirty days of trash is real disk.** A deleted book's PNGs sit in `.trash/` until the sweep.
  At the scale of this archive that is megabytes, not gigabytes, and the sweep is Plan 1's, but the
  vault is Syncthing-backed and a user who deletes twenty books in a week will notice the free-space
  figure before the sweep runs.
- **A delete during an active sync.** If the device is mid-sync when a book is deleted, its in-flight
  push may land before the suppression is committed and re-create one progress row. The suppression
  stops the next one, and the stray row is visible in `/log`. Taking a lock across the device's whole
  sync cycle to close this would be far more invasive than the one row it saves.

---

## Hot path budget

`POST /syncs/progress` and `PUT /syncs/progress` are what the X4 talks to. Everything this plan adds
to that handler goes in **one** helper, called after `_store.upsert()` and before
`_write_progress_to_vault()`:

```python
def _maintain_reading_data(update: ProgressIn, record: dict) -> None:
    """Derive sessions, rollups and book status for one new progress row.

    Never raises: the device's sync must not fail because a rollup did.
    """
    if os.getenv("READING_DERIVE", "1") != "1":
        return
    started = time.monotonic()
    try:
        from xteink_service import books, reading
        reading.init_db()
        books.init_db()
        days = reading.extend_for_update(
            update.document,
            (update.device_id or update.device or ""),
            record["timestamp"],
        )
        reading.recompute_days(days)
    except Exception as exc:
        logger.warning("Reading-data maintenance failed for %s: %s",
                       update.document[:12], exc)
    finally:
        elapsed = time.monotonic() - started
        if elapsed > 1.0:
            logger.warning("Reading-data maintenance took %.2fs for %s",
                           elapsed, update.document[:12])
        else:
            logger.debug("Reading-data maintenance: %.0f ms", elapsed * 1000)
```

Why this is bounded:

- `extend_for_update()` runs one indexed `MAX(timestamp)` lookup, one indexed range `SELECT` over
  one `(document, device_key)` window, one `DELETE` over the same window, and at most a handful of
  `INSERT`s. The window is bounded by the number of pushes since the last gap longer than 30
  minutes, which is one sitting, which is tens of rows.
- `recompute_days()` recomputes at most two dates (one sitting can straddle midnight), each a single
  indexed scan over one day's sessions.
- `books.apply_session()` is a handful of single-row reads and writes keyed on `book_slug`.

Why it cannot fail the request:

- The whole body is inside `try` / `except Exception`, and the handler returns `_kosync_view(record)`
  regardless. The progress row is already committed before this runs, so a maintenance failure loses
  derived data, never the reading position. The next push, or any rebuild endpoint, repairs it.
- `READING_DERIVE=0` is an operational kill switch for the device-facing path. It is the only new
  environment variable in this plan and it exists only because this code runs inside the sync
  handler. Add it to `docker-compose.yml` as a commented-out line, not an active one.

Consistency with existing behaviour: `put_progress` is already an `async def` doing synchronous
`sqlite3` work (`record_device`, `_latest`, `upsert`, and `_record_client_title`, which constructs a
`SyncState` against `state.db`). This adds more of the same rather than introducing
`asyncio.to_thread`, which would change the SQLite locking picture for no measured benefit. If the
1-second warning above ever fires in production, that is the signal to revisit it, and the plan is
to move maintenance behind a small queue drained outside the request, not to make the database
access async.

The de-dupe early return in `put_progress` (identical `progress` and `percentage` as the last row)
stays ahead of all of this and writes nothing, so a device re-sending a position it already reported
costs nothing extra.

---

## Testing plan

House rule from [AGENT.md](AGENT.md): all tests run inside Docker.

```bash
docker run --rm xteink-service:dev python -m pytest tests/test_reading_sessions.py -v
```

`conftest.py` pins `STATE_DB` and `KOREADER_DB` to temp files before import. New test modules follow
the `api_env` fixture pattern from [tests/test_api.py](tests/test_api.py): a real sqlite file under
`tmp_path`, `monkeypatch.setenv` before importing the app, tables created by hand. Do not add
`hypothesis` or any other test dependency; the property-style cases below use `random.Random(seed)`
with a fixed seed, which is deterministic and needs nothing new in `pyproject.toml`.

### Shared fixtures

Put these in a new `tests/conftest_reading.py` imported by the reading tests, or inline them in the
first file that needs them:

- **`reread_history`** - one document, one device, three phases: a run from 0.02 to 0.97 over ten
  days, a gap of two weeks, then a run starting at 0.03 again. Exercises the only re-read signal
  the system has.
- **`out_of_order_push`** - a document with updates at `t`, `t+600`, `t+1200`, then a row inserted
  with `timestamp = t+300` and a higher `id`. Must land inside the first session and must not create
  a new one.
- **`two_devices_one_book`** - the same `document` hash pushed by `device_id="crosspoint-reader"`
  and by `device_id="sticky"` (via the `X-Device-Name` header path), interleaved in time. Must
  produce two separate session streams, never one with a negative `pct_delta`.
- **`two_hashes_one_book`** - two different `document` hashes whose `document_aliases.title` values
  canonicalise to the same slug. Must produce two session streams that aggregate into one book in
  every book-level view.

### Per item

**2.1, `tests/test_reading_sessions.py`**

- `test_single_update_is_a_zero_duration_session`
- `test_updates_within_gap_form_one_session`
- `test_update_beyond_gap_starts_new_session` (exactly `SESSION_GAP_S` stays, `+1` splits)
- `test_equal_timestamps_are_ordered_by_id` (two rows, same second, reversed insert order)
- `test_session_crossing_midnight_uses_start_date`
- `test_pct_delta_can_be_negative_on_backwards_navigation`
- `test_pages_delta_null_without_page_count`
- `test_pages_delta_recomputed_when_page_count_arrives_later`
- `test_rederive_is_idempotent` - derive, snapshot all columns except `id` and `derived_at`, derive
  again, assert equal. **Property-style:** loop 50 seeds generating random timestamp sequences
  (gaps drawn from 60 s to 7200 s, percentages monotonic with occasional dips), assert the snapshot
  comparison for every seed.
- `test_rederive_window_does_not_touch_older_sessions` - record `ids` of sessions ending before the
  anchor, extend, assert those ids are unchanged.
- `test_out_of_order_row_joins_existing_session` (uses `out_of_order_push`)
- `test_two_devices_produce_separate_sessions` (uses `two_devices_one_book`)
- `test_book_slug_filled_from_alias`, `test_relabel_document_updates_existing_sessions`
- `test_rebuild_from_empty_matches_incremental` - push N updates one at a time through
  `extend_for_update`, snapshot; wipe; `rebuild(None)`; assert identical. This is the single most
  valuable test in the file, because it proves the incremental and batch paths agree.

**2.2, `tests/test_reading_rollup.py`**

- `test_recompute_day_is_idempotent` (all columns except `updated_at`)
- `test_empty_day_writes_no_row` (the `HAVING COUNT(*) > 0` guard)
- `test_negative_pct_delta_clamped_to_zero_in_pct_sum`
- `test_books_touched_counts_distinct_slugs_ignoring_empty`
- `test_books_finished_counts_attempts_by_local_date`
- `test_reading_stats_matches_a_naive_scan` - compute the same aggregates with a plain Python pass
  over the seeded sessions and assert the endpoint agrees. Guards against the rollup silently
  diverging from the thing it replaced.
- `test_reading_stats_response_keeps_existing_keys` - assert `books`, `read_pct`, `pages_read`,
  `books_read` are all still present with the same nested keys, so
  [web/src/lib/api.ts](web/src/lib/api.ts) `ReadingStats` stays valid.
- `test_streak_current_allows_missing_today`, `test_streak_longest_run`

**2.3, `tests/test_books_table.py` and `tests/test_tbr_migration.py` and `tests/test_reading_attempts.py`**

- `test_backfill_slugs_is_idempotent`, `test_backfill_slugs_force_recomputes`
- `test_books_backfilled_from_screenshots_and_aliases`
- `test_alias_only_book_appears_without_screenshots` (the Kindle case)
- `test_screenshot_and_alias_titles_merge_into_one_row` (the canonical-slug join)
- `test_auto_status_does_not_overwrite_manual`
- `test_clear_override_reverts_to_derived_status`
- `test_manual_only_statuses_rejected_from_auto_path` (`on_hold`, `skimmed`, `abandoned`)
- `test_invalid_status_rejected_with_400`, `test_rating_out_of_range_rejected`
- `test_linked_documents_uses_slug_column`, `test_linked_documents_falls_back_without_backfill`
- `test_book_titles_for_slug_uses_index` (assert the result, and assert the old
  `SELECT DISTINCT book_title` with no `WHERE` is gone by checking the function's SQL does not run
  on a table with 10k seeded rows in unreasonable time, or more simply by asserting correctness and
  leaving performance to manual verification)

  `tests/test_tbr_migration.py`:
- `test_fold_preserves_every_tbr_row`
- `test_fold_is_idempotent` - run twice, assert row count and all fields unchanged
- `test_fold_merges_into_existing_book_row` (a TBR entry for a book that already has screenshots)
- `test_fold_does_not_resurrect_soft_deleted_book`
- `test_fold_maps_queued_to_want_to_read_manual`
- `test_fold_lets_device_win_for_reading_and_done_with_progress`
- `test_tbr_shim_returns_same_shape` - assert every key of the current `/api/tbr` response is present
  with the same types, including `id`, `status` in `{queued, reading, done}`, and `added_at`
- `test_tbr_shim_ids_stable_across_restart`
- `test_tbr_delete_clears_on_list_without_deleting_book`
- `test_tbr_books_table_is_not_written_after_fold` - snapshot `tbr_books` contents, exercise every
  `/api/tbr` verb, assert unchanged

  `tests/test_reading_attempts.py`:
- `test_first_session_opens_attempt_one`
- `test_crossing_finished_threshold_closes_attempt` (`finished_at` is the first qualifying update)
- `test_restart_after_finish_opens_attempt_two` (uses `reread_history`)
- `test_backwards_navigation_without_finish_does_not_open_attempt`
- `test_attempt_derivation_is_idempotent` (replay the same sessions, assert one set of rows)
- `test_rebuild_preserves_manual_attempts` (only `source='auto'` rows are deleted)
- `test_status_follows_attempts` (`reading` then `read` then `rereading`)

**2.4, `tests/test_stats_api.py`**

- `test_heatmap_bins_are_absolute_not_relative` - two fixtures, one quiet year and one heavy, assert
  the same 20-minute day gets the same `level` in both
- `test_low_confidence_flag_under_min_active_days` and `test_cleared_at_threshold`
- `test_pace_excludes_zero_duration_sessions`
- `test_pace_pages_basis_counts_books_with_page_counts`
- `test_rhythm_buckets_are_24_and_7`, `test_session_attributed_to_start_hour`
- `test_goal_round_trips_through_settings`, `test_projection_flagged_low_confidence_early_in_year`
- `test_trace_returns_one_point_per_session_in_order`
- `test_stats_routes_refused_on_public_sync_host` - set `PUBLIC_SYNC_HOST`, request `/api/stats/overview`
  with that `Host` header, assert 404. **This is the important one.** It proves 2.4's routes were
  not accidentally added to `_PUBLIC_ALLOWED`.

**2.5, `tests/test_writing.py`**

- `test_create_requires_non_empty_body`, `test_body_of_only_anchors_is_allowed`
- `test_entry_date_defaults_to_today`, `test_position_pct_defaults_to_current_position`
- `test_soft_delete_hides_from_list_and_restore_returns_it`

  Anchors and reconciliation:
- `test_anchor_regex_matches_token_and_skips_escaped` (`((ref:12))` versus `\((ref:12))`)
- `test_update_drops_ref_whose_anchor_was_removed` - save a body with two anchors, save again with
  one, assert the other `writing_refs` row is gone and `refs_dropped` is 1
- `test_update_strips_anchor_with_no_ref_row` and
  `test_update_strips_anchor_belonging_to_another_entry` - assert the token is removed from the
  stored body and counted in `anchors_stripped`
- `test_orphan_ref_created_by_picker_is_dropped_on_next_save`
- `test_positions_renumbered_by_first_appearance`

  Snapshots, which are the point of the item:
- `test_snapshot_is_copied_by_server_not_client` - POST a ref with a `snapshot` key in the body,
  assert the stored value came from the source row and not from the request
- `test_snapshot_unchanged_when_clipping_text_is_edited`
- `test_snapshot_unchanged_when_ocr_corrected_is_saved`
- `test_snapshot_unchanged_when_clipping_is_tombstoned`

  `source_state` transitions:
- `test_state_live_when_text_matches`
- `test_state_deleted_when_clipping_tombstoned` and `test_state_deleted_when_screenshot_removed`
- `test_deleted_clipping_leaves_entry_readable` - tombstone the clipping, assert `GET` still returns
  the full body and the original quoted text, and that the vault render still contains it
- `test_state_drifted_when_text_differs`
- `test_rehash_after_match_method_switch_repoints_and_stays_live` - re-insert the same `clip_id`
  under a second document hash that resolves to the same `book_slug`, tombstone the first, assert
  the ref repoints and stays `live`
- `test_rehash_with_changed_text_reports_drifted`
- `test_refresh_updates_snapshot_and_returns_previous`
- `test_refresh_on_deleted_ref_returns_409`
- `test_resolution_never_writes_snapshot` - patch the connection to fail any `UPDATE` touching
  `snapshot`, run a list request over live, drifted, and deleted refs

  Vault:
- `test_vault_entry_written_under_date_heading`
- `test_vault_entry_rewritten_in_place_on_edit` - edit twice, assert exactly one
  `<!-- writing:{id} -->` marker pair in the file
- `test_vault_entry_removed_on_soft_delete`
- `test_clipping_anchor_expands_to_blockquote_with_locator`
- `test_screenshot_anchor_expands_to_obsidian_embed` - assert `![[The Hidden Keys/....png]]`,
  the same form `append_to_daily_note` writes
- `test_vault_rebuild_replays_writing_with_anchors_expanded_without_duplicating`
- `test_vault_write_failure_is_non_fatal` - point `VAULT_PATH` at a non-directory, assert the POST
  still returns 200 and the row exists (matching how `api.create_highlight` behaves)

**2.6, `tests/test_clip_links.py`**

- `test_link_persisted_on_first_decorate`
- `test_second_decorate_does_not_rebuild_index` - monkeypatch `_load_books` to raise, assert the
  second call still returns the stored ids
- `test_status_exact_when_position_and_text_agree`
- `test_status_repaired_when_only_one_signal`
- `test_status_failed_when_book_has_screenshots_and_nothing_matches`
- `test_status_pending_when_book_has_no_screenshots`
- `test_version_bump_forces_recompute`
- `test_clip_revision_change_forces_recompute`
- `test_ocr_correction_invalidates_book_links`
- `test_alias_change_invalidates_document_links`
- `test_relink_force_recomputes_everything`
- `test_missing_table_degrades_to_live_matching` - drop the table, assert
  `attach_screenshot_ids` still returns the right ids
- `test_sync_router_never_writes_links` - snapshot the link table, run a full device sync sequence
  (`GET` probe, `PUT` tombstone, `PUT` item, `GET` page), assert the table is unchanged

**2.7, `tests/test_erase.py` and `tests/test_suppressions.py`**

`tests/test_erase.py`, undo and re-derivation:

- `test_undo_restores_progress_rows_byte_identically` - snapshot every column of every
  `progress_updates` row for a document, delete them, undo, assert the rows compare equal column by
  column **including `id`**. The `id` assertion is the point: `AUTOINCREMENT` must not have handed
  the ids out again, because 2.1's `ORDER BY timestamp, id` depends on them.
- `test_undo_is_idempotent` - call `POST /api/undo` with the same token twice; the second call
  returns 409 and the database is byte-identical to the state after the first.
- `test_rederivation_after_delete_matches_full_rebuild` - seed a multi-week history, delete a
  range, let the narrow path re-derive, snapshot `reading_sessions` and `reading_daily_stats`; then
  wipe both and run `POST /api/sessions/rebuild` with `{"from": null, "book": null}`; assert
  identical on every column except `id` and `derived_at`. **The most valuable test in the file**,
  for the same reason `test_rebuild_from_empty_matches_incremental` is in 2.1's.
- `test_rederivation_window_is_narrow` - record the `ids` of sessions ending at or before the anchor,
  delete a row well after it, assert those ids are untouched and that the number of re-derived
  sessions is bounded by the window rather than by the history. This is the test that proves the
  narrow window is actually narrow, not just correct.
- `test_edit_timestamp_moves_entry_between_days` - both the old and the new `local_date` appear in
  `days_recomputed`, and both rollup rows are correct afterwards.
- `test_edit_rejects_progress_field` - sending `progress` is a 400.
- `test_edit_document_requires_existing_alias` - an unknown hash is a 409 and writes nothing.
- `test_undo_restores_attempts_and_status` - delete progress that closed an attempt, undo, assert
  `reading_attempts` and `books.status` are back and that a `status_source = 'manual'` status was
  never touched in either direction.

`tests/test_erase.py`, book delete:

- `test_book_delete_dry_run_writes_nothing` - snapshot every path, size, and mtime under the temp
  vault and every row count in both databases before and after a dry run, assert exact equality.
  Also patch `builtins.open` to raise on any write mode and confirm the dry run still succeeds.
  This is 3.7's assertable property applied to this endpoint.
- `test_book_delete_requires_dry_run_or_confirm` - neither flag is a 400 and writes nothing.
- `test_vault_files_moved_not_unlinked` - after a delete, assert the note and every PNG exist under
  `.trash/<token>/` with their original relative paths, and that nothing under the live roots
  remains. Patch `Path.unlink` to raise so a stray unlink fails the test outright.
- `test_undo_restores_vault_files` - undo, assert every moved file is back at its original path with
  identical bytes and that `.trash/<token>/` is empty or gone.
- `test_book_delete_tombstones_clippings_without_removing_rows` - seed live clippings, delete the
  book, assert `SELECT COUNT(*) FROM clippings` is unchanged, every row has `deleted = 1`, every
  `text` is empty, every `trashed_text` is populated, and every tombstone took a fresh revision
  strictly greater than the one before it.
- `test_book_delete_soft_deletes_books_row` - the `books` row survives with `deleted_at` set, so a
  later `fold_tbr()` cannot resurrect it. Run `fold_tbr()` afterwards and assert it does not.
- `test_daily_stats_recomputed_not_deleted` - a day that also holds another book keeps its row with
  the other book's totals intact.

`tests/test_suppressions.py`:

- `test_suppression_stops_replayed_push` - delete all progress for a document, replay the exact same
  push through `POST /syncs/progress`, assert no row is written, the response is a valid kosync body,
  and the device is still recorded in `devices`.
- `test_suppression_blocks_restored_rows_from_rederiving` - re-insert the deleted rows directly into
  `progress_updates` (simulating a `koreader.db` restore), run `POST /api/sessions/rebuild`, assert
  the suppressed rows produce no sessions and no rollup rows.
- `test_undo_removes_suppression_and_push_lands_again` - undo, replay the same push, assert it is
  stored and derived normally.
- `test_ignore_before_not_written_for_partial_range` - delete a row in the middle of a history,
  assert `suppression` is null and the response carries the reason.
- `test_ignore_before_takes_max_on_conflict` - two qualifying deletes, assert the stored `since` is
  the larger and that `ignore_all` is never downgraded to `ignore_before`.
- `test_clipping_tombstone_is_never_suppressed` - a `PUT` containing a tombstone is applied even
  under an `ignore_before` suppression.
- `test_suppressed_clipping_upload_still_returns_accepted` - every item dropped, response is still
  `{"sync_version": 2, "accepted": 1}`. The firmware aborts its whole sync otherwise.
- `test_clipping_suppression_retires_when_cursor_passes` - page the collection down past `since`,
  assert the suppression row is gone and a subsequent upload is accepted.
- `test_screenshot_suppression_skips_group_without_downloading` - monkeypatch `_download_file` to
  raise, run `run_sync()` against a suppressed book, assert it completes and archives nothing.
- `test_missing_suppressions_table_degrades` - drop `sync_suppressions`, assert
  `POST /syncs/progress`, the clipping `PUT`, and `run_sync()` all behave exactly as they do with no
  suppression rather than raising.

### Frontend Vitest

Config is in [web/vite.config.ts](web/vite.config.ts) (`include: ['src/**/*.test.ts']`), so new
tests go next to the module they test.

- `web/src/lib/heatmap.test.ts`:
  `heatmapLevel` returns 0 for 0, 1 for 1 and 15, 2 for 16 and 30, 3 for 31 and 60, 4 for 61 and up;
  bins are the exported constant; a value of exactly a bin edge lands in the lower bin.
- `web/src/lib/calendar.test.ts`: `buildMonth(2026, 2)` (February, non-leap) returns 28 dated cells
  and pads to whole weeks; `buildMonth(2024, 2)` returns 29.
- `web/src/lib/api.test.ts` additions, following the existing `mockFetch` pattern:
  `api.sessions.list` builds the right query string; `api.books.update` sends `PUT` with a JSON
  body; `api.writing.create` posts to `/api/books/{slug}/writing` with the slug encoded;
  `api.clippings.relink` posts to `/api/clippings/relink`; `api.stats.heatmap` encodes `from` and
  `to`; `api.progress.update` sends `PUT` to `/api/progress/{id}`; `api.books.remove` appends
  `dry_run=1` for the preview and `confirm=1` for the apply and never both.

### Manual verification

1. `docker compose build && docker compose up -d`, then `docker compose logs -f xteink`.
2. `curl -s localhost:8090/api/books/rebuild -X POST -d '{"book":null}' -H 'Content-Type: application/json' | jq`
   and confirm `slugs_backfilled` matches the row counts in `synced_screenshots` and
   `document_aliases`.
3. `curl -s localhost:8090/api/sessions/rebuild -X POST -d '{"from":null,"book":null}' -H 'Content-Type: application/json' | jq`.
4. Open `/tbr` and confirm every book that was there before is still there, in the same order, with
   the same Done section contents.
5. Open `/books` and confirm no book appears twice and the sidebar index is unchanged.
6. Open `/stats` and check the heatmap against `/log` for a week you remember.
7. Read on the X4 for a few minutes with the sync plugin on, then:
   `docker compose logs xteink | grep "Reading-data maintenance"` and confirm the debug line shows a
   small number of milliseconds and that the 1-second warning never fires.
8. Open a book's Clippings tab twice and confirm the second load is visibly faster, then check
   `SELECT status, COUNT(*) FROM clipping_screenshot_links GROUP BY status`.
9. Write an entry on the Writing tab, cite a clipping and a screenshot from the picker, edit it,
   then delete it, checking `Books/<title>.md` after each step for exactly zero or one marker pair,
   a blockquote for the clipping, and an `![[...]]` embed for the screenshot.
10. Re-run steps 2 and 3. Nothing user-visible should change. That is the idempotency check that
    matters.

### Regression risks against existing tests

Named in rough order of likelihood:

- **[tests/test_api.py](tests/test_api.py)** is the most likely to break. Its `api_env` fixture
  creates `synced_screenshots`, `document_aliases`, and `progress_updates` **by hand**, without
  `book_slug`, without `books`, and without any of the new tables. Every code path that assumes a
  new column exists will fail there. Mitigation: every entry point calls its module's `init_db()`
  first, and `init_db()` does the `ALTER TABLE ... ADD COLUMN` in `try` / `except
  sqlite3.OperationalError`, so the fixture's hand-made tables get upgraded on first touch.
  `test_list_books`, `test_reading_log_*`, and the TBR tests in that file will all need their
  assertions widened for the new response keys.
- **[tests/test_koreader_sync.py](tests/test_koreader_sync.py)** sets `os.environ["KOREADER_DB"]` at
  module level and imports `app` and `_store` immediately. It never sets `STATE_DB`, so it inherits
  the one `conftest.py` pins, which has **no tables at all** when that module runs first.
  `_maintain_reading_data` must therefore survive a completely empty `state.db`, which is what the
  `init_db()` call at the top of the helper and the surrounding `try` / `except` guarantee. Add an
  explicit `test_progress_sync_survives_empty_state_db` to that file. 2.7 adds a second thing to
  that path, the `suppressions.get()` lookup, which runs against the same empty `state.db` and must
  return "no suppression" on `sqlite3.OperationalError` rather than raising. The same test covers
  both once it asserts a stored row and a valid kosync body.
- **[tests/test_clip_match.py](tests/test_clip_match.py)** asserts specific `screenshot_id` values
  returned by the UI routes. Persistence must not change any of them. It calls
  `xteink_service.clippings.init_db()` directly in its fixture, so adding the link table there is
  the right hook. Its `env` fixture also hand-creates `synced_screenshots` without `book_slug`,
  so `_load_books()` must tolerate an empty slug column by falling back to the current
  canonicalisation (or the fixture gets the `ALTER TABLE` through `init_db()`, which is cleaner).
- **[tests/test_clippings.py](tests/test_clippings.py)** asserts the device-facing protocol. The
  `test_sync_router_never_writes_links` case above exists specifically to keep this file honest.
  Nothing in 2.6 should change any assertion here.
- **[tests/test_vault_writer.py](tests/test_vault_writer.py)** and
  **[tests/test_vault_integration.py](tests/test_vault_integration.py)** assert the exact contents
  of `Books/<title>.md` and the daily notes. Writing markers only appear when a writing entry is
  written, so these should pass untouched, but any test that asserts a whole-file equality rather
  than a substring will need checking.
- **[tests/test_full_pipeline.py](tests/test_full_pipeline.py)** is the `--live` device test and runs
  the archiver end to end. The new archiver invalidation hook must not raise when
  `clipping_screenshot_links` does not exist.
- **[tests/test_book_pages.py](tests/test_book_pages.py)** exercises `resolve_book_pages()`, which
  gains a `reading.refresh_pages_for_title()` call. That call must be inside a `try` / `except` so a
  missing `reading_sessions` table does not fail page resolution.
- **[tests/test_state.py](tests/test_state.py)** constructs `SyncState(tmp_path / "state.db")`
  directly and asserts `mark_synced` / `set_title` behaviour. Both gain a `book_slug` write, so add
  assertions rather than expecting the existing ones to break.
- **[web/src/lib/api.test.ts](web/src/lib/api.test.ts)** asserts exact fetch URLs. Existing cases
  are unaffected because no existing path changes.

---

## Acceptance criteria

**Data model**

- [ ] `reading_sessions`, `reading_daily_stats`, `books`, `reading_attempts`, `writing_entries`,
      `writing_refs`, `clipping_screenshot_links`, `settings`, and `sync_suppressions` all exist in
      `state.db` and in no other database.
- [ ] `writing_refs.snapshot` is written only by the add-ref and refresh endpoints. No read path,
      resolution pass, or delete path ever writes it.
- [ ] Removing an anchor from an entry body drops its `writing_refs` row on save, and an anchor with
      no ref row is stripped from the stored body.
- [ ] `progress_updates` has no `deleted_at` column and no soft-delete predicate on any read.
- [ ] There is exactly one undo table, one `POST /api/undo`, and one token format, all owned by
      Plan 1 item 1.3. `grep -rn 'CREATE TABLE.*undo' xteink_service/` matches one file.
- [ ] `synced_screenshots.book_slug` and `document_aliases.book_slug` exist, are indexed, and are
      populated for every non-empty title.
- [ ] `koreader.db` has exactly two new indexes on `progress_updates` and no new tables.
- [ ] No `ATTACH DATABASE` anywhere in `xteink_service/`.
- [ ] Every new module resolves `STATE_DB` and `KOREADER_DB` inside a function. `grep -rn
      'os.getenv("STATE_DB"' xteink_service/` shows no module-level assignment outside `ProgressStore`.
- [ ] Every schema statement is `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, or
      `ALTER TABLE ... ADD COLUMN` inside `try` / `except sqlite3.OperationalError`.

**Derivation**

- [ ] `POST /api/sessions/rebuild` run twice produces identical rows on every column except `id` and
      `derived_at`.
- [ ] Feeding the same updates one at a time through the kosync handler produces the same sessions
      as one batch rebuild.
- [ ] A late or out-of-order row joins the correct existing session and creates no duplicate.
- [ ] Two devices pushing the same document produce separate sessions, and no session has a
      `pct_delta` caused by interleaving.
- [ ] A re-read (finish, then restart below 10 percent) opens `attempt_no = 2` and sets the book's
      auto status to `rereading`.
- [ ] A backwards jump without a prior finish opens no attempt.

**Performance**

- [ ] `GET /api/reading-stats` issues no query without a `WHERE` or `LIMIT` against
      `progress_updates`.
- [ ] `GET /api/books` is a single query against `books` with no Python merge loop.
- [ ] `_linked_documents()` and `_book_titles_for_slug()` use the indexed `book_slug` columns.
- [ ] A second load of a book's Clippings tab builds no `_BookIndex`.
- [ ] The `Reading-data maintenance took` warning does not fire during normal reading.

**Device path**

- [ ] `POST /syncs/progress` returns a valid kosync response even when every new table is missing.
- [ ] A raised exception inside reading-data maintenance is logged and swallowed, and the progress
      row is still stored.
- [ ] `_PUBLIC_ALLOWED` is unchanged: `("/syncs", "/users", "/health", "/api/v1/clippings")`.
- [ ] No new route is reachable on `PUBLIC_SYNC_HOST`, proven by a test.
- [ ] `sync_router` writes nothing to `clipping_screenshot_links`.
- [ ] A suppressed push still registers the device, still returns a valid kosync body, and writes no
      `progress_updates` row.
- [ ] A clipping `PUT` whose every item was suppressed still returns
      `{"sync_version": 2, "accepted": 1}`, and a tombstone is never suppressed.
- [ ] Dropping `sync_suppressions` degrades every consulting path to today's behaviour instead of
      raising.

**TBR fold**

- [ ] Every row that was in `tbr_books` has a corresponding `books` row with `on_list = 1` and a
      matching `tbr_id`.
- [ ] `fold_tbr()` run twice changes nothing.
- [ ] A soft-deleted book is not resurrected by re-running the fold.
- [ ] `/tbr` renders identically before and after, with no change to
      `web/src/routes/tbr/+page.svelte` or `web/src/lib/components/TbrRow.svelte`.
- [ ] `tbr_books` still exists on disk, is unchanged after exercising every `/api/tbr` verb, and
      `_ensure_tbr()` has been deleted.

**Deleting and editing**

- [ ] Every destructive endpoint in 2.7 returns an `undo_token`, and `POST /api/undo` restores
      `progress_updates` rows identical on every column including `id`.
- [ ] Undo is idempotent: a second call with the same token returns 409 and changes nothing.
- [ ] Re-derivation after an edit or a delete produces the same `reading_sessions` and
      `reading_daily_stats` as a full `POST /api/sessions/rebuild`, and touches no session ending at
      or before the anchor.
- [ ] A book delete tombstones its clippings and removes no clipping row.
- [ ] A book delete sets `source_state = 'deleted'` on citations of the rows it removed, removes no
      `writing_refs` row, and overwrites no `snapshot`.
- [ ] A writing entry about a different book that cites the deleted book is not deleted, not edited,
      and still renders its quoted text.
- [ ] A book delete soft-deletes the `books` row, so re-running `fold_tbr()` cannot resurrect it.
- [ ] A book-delete dry run provably writes nothing and moves no file, asserted by both a filesystem
      snapshot and a patched `open`.
- [ ] Vault files are moved into `VAULT_PATH/.trash/<token>/` with their relative paths preserved,
      never unlinked, and undo puts every one of them back byte-identical.
- [ ] `.trash/` is in the vault's `.stignore` and is not `(?d)`-marked.
- [ ] A progress suppression is written only when every row at or before `since` is gone, and a
      partial-range delete writes none and says why in the response.
- [ ] Undo removes the suppressions its own operation wrote and restores any it overwrote.
- [ ] `GET /api/undo/recent` does not select the `payload` column.

**UI**

- [ ] `/stats` exists, is linked from the sidebar and the bottom nav, and renders with no data
      without throwing.
- [ ] Heatmap bins are absolute, and a range with fewer than 14 active days renders muted with a
      low-confidence note.
- [ ] Every chart colour comes from a CSS custom property, and switching the theme recolours it with
      no JavaScript.
- [ ] `web/package.json` dependencies are unchanged, or, if LayerCake was adopted, the change is
      called out in the pull request along with the `npx cap sync` requirement.
- [ ] A book page shows status, rating, attempts, a writing tab, and a progress trace.
- [ ] Minutes are labelled as time with the book open, not as time spent reading.
- [ ] The reading log offers a per-entry edit and delete, and the book page has a collapsed danger
      zone with progress delete and book delete.
- [ ] Every delete in 2.7 surfaces its undo through the single shared `UndoToast.svelte` from Plan 1
      item 1.3. There is no second toast and no per-page undo button.
- [ ] Delete copy says recording stops, never that the reader is cleared, and an active suppression
      is visible on the book page with a control to clear it.

**Housekeeping**

- [ ] `ruff check` passes at `line-length = 100`.
- [ ] `clip_match.py`'s module docstring no longer claims nothing is persisted.
- [ ] `ARCHITECTURE.md` gains the new tables; `CLAUDE.md` gains a line in "Architecture decisions"
      about derived sessions measuring elapsed time, not attention.
- [ ] No em dashes in any new UI string, comment, or document.

---

## Out of scope

Explicitly not in this plan. Some are in sibling plans, some are not being done at all.

| Thing | Where it belongs |
| --- | --- |
| FTS5 search, export menus, trash page, "on this day", placeholder covers, OKLCH token restructure | Plan 1 |
| Book metadata providers beyond `book_pages.py`, cover download and serving, job progress widget, command palette, windowed lists, URL-synced view state, preview-then-apply, PWA, share poster | Plan 3 |
| Readwise, Hardcover, StoryGraph, OPDS, semantic search | `IDEAS.md` Phase 4, speculative |
| A migration framework | Not happening. Idempotent `CREATE IF NOT EXISTS` plus guarded `ALTER TABLE` is the house pattern and it is sufficient at this schema size. |
| Async SQLite (`aiosqlite`) | `aiosqlite` is declared in `pyproject.toml` and unused. This plan does not start using it, and nothing here should make anyone want to. |
| Dropping `tbr_books` | A later chore. The table stays on disk as the rollback copy for at least one release. |
| A scheduler or background worker | Nothing here needs one. `on_hold` is manual precisely so that no sweep job is required. |
| Per-book-per-day rollup table | Deliberately omitted. Per-book views read `reading_sessions` through `idx_sessions_slug_start`, which is bounded and does not need a second table to keep in sync. |
| Exposing `SESSION_GAP_S` as a setting | Not until there is evidence it needs tuning. It is stored per session row so a future change is auditable. |
| Inferring sessions from anything other than position reports | The device sends no session or duration data. There is nothing else to infer from. |
| A whole-book page number from the device | Impossible. `CLAUDE.md` is explicit: the rich `position` object's `page` and `pages` are within the current spine item and layout-dependent, and clipping `start_page` / `end_page` have the same per-chapter meaning. `book_pages.py` inference is the only source and stays necessary. |
| Multi-user anything | Single user behind a reverse proxy. No auth is added to any new route. |
