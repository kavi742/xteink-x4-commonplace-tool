# Plan 1: Quick Wins

This document expands "Phase 1: cheap wins" from [IDEAS.md](IDEAS.md) into an implementable
specification. It covers six self-contained items: FTS5 search (1.1), passage export (1.2), soft
delete plus an undo toast and a trash view (1.3), "on this day" and a review queue (1.4),
procedural placeholder covers (1.5), and an OKLCH token system for the stylesheet (1.6). Each item
is sized in days, not weeks, and none of them requires a new Python dependency, a schema migration
framework, or a change to the device-facing sync protocols.

**Naming note.** The "Plan 1 / Plan 2 / Plan 3" numbering in this document set is NOT the same as
the historical build phases 1 to 11 recorded in [TESTING.md](TESTING.md) and the phase table in
[CLAUDE.md](CLAUDE.md). Those phases describe how the service was originally built (foundation,
device discovery, status display, screenshot archiving, kosync server, dedup state, vault writer,
observability, web UI, integration, clipping sync) and are all complete. The Plan numbers here are
forward-looking work packages. Where this document says "1.3" it means item 1.3 of Plan 1, not
build phase 1 or 3.

---

## Ordering and dependencies

| Item | Depends on | Can start immediately | Notes |
|---|---|---|---|
| 1.1 FTS5 search | none | yes | Lands first. Creates `xteink_service/search_index.py`, which 1.2, 1.3, and 1.4 then touch. |
| 1.2 Export | 1.1 (soft) | yes, but see note | Pure read path over `highlights`, `clippings`, `synced_screenshots`, `writing_entries`. No schema change. It imports `expand_anchors()` from `search_index.py`, so landing 1.1 first saves writing that function twice. If 1.2 lands first, define the function in `search_index.py` anyway and have 1.1 use it. |
| 1.3 Soft delete + undo + trash | 1.1 (soft) | yes, but see note | Independent of 1.1 functionally. If 1.1 has landed, 1.3 must add `AND h.deleted_at IS NULL` to the highlight indexer and call `search_index.unindex()` on delete. If 1.3 lands first, 1.1 writes that predicate from the start. 1.3 also creates `xteink_service/undo.py`, the `undo_log` table, and `POST /api/undo`, which `PLAN-2-READING-DATA.md` registers further kinds against, so 1.3 must land before any Plan 2 item that wants undo. |
| 1.4 On this day + review | none | yes | Reads the same three tables as 1.1 but does not use the FTS index. If 1.3 has landed, its queries must exclude soft-deleted rows. |
| 1.5 Placeholder covers | none | yes | Frontend only. No backend work, no new endpoint. |
| 1.6 OKLCH tokens | none | yes | Frontend only. Lands cleanest BEFORE 1.5 so the cover palette can reference the new tokens, but 1.5 can also ship with literal `oklch()` values and be retrofitted. |

Recommended landing order, matching the "Suggested order" section of [IDEAS.md](IDEAS.md):

1. **1.1** (unblocks the most, fixes the worst current scan)
2. **1.3** (fixes a known bug on the way: orphaned `==` marks)
3. **1.6** then **1.5** (a day each, visible on every page)
4. **1.4**
5. **1.2**

Nothing in Plan 1 touches `koreader.db`. See "Database boundary" below.

### Seams for Plan 2 and Plan 3

Two sibling documents are being written. Do not design their contents here, but leave these seams
intentionally:

- **`search_index` (1.1) is a prerequisite for the command palette in `PLAN-3-POLISH.md` (3.4).**
  The palette needs a ranked, bounded, debounced multi-kind query. `GET /api/search` with the
  `kinds` facet and `limit` cap is that query. Do not build palette-specific ranking here.
- **The soft-delete pattern from 1.3 is reused by the writing log in `PLAN-2-READING-DATA.md`
  (2.5).** 2.5 adds two tables, `writing_entries` (a long-form entry about one book) and
  `writing_refs` (one row per citation inside an entry, carrying a `snapshot` of the cited text and
  a `source_state` of `live`, `deleted`, or `drifted`). `writing_entries` carries its own
  `deleted_at` column and appears in the same `/trash` page, so `GET /api/trash` stays shaped as a
  dictionary of named buckets, not a flat array, and 1.3 adds the `writing` bucket itself rather
  than leaving room for it.
  Three things in Plan 1 are **obligations, not optional extras**: the `writing` value of
  `search_docs.kind` in 1.1, the `writing` value of `include` in 1.2, and the `writing` trash
  bucket plus the `writing_delete` undo contract in 1.3. Plan 1 does not design the writing log,
  the editor, the reference picker, or the vault render; it only guarantees that writing is
  searchable, exportable, and recoverable on the day 2.5 lands.
- **`undo_log` and its reversal registry are defined in 1.3, not in Plan 2.**
  [xteink_service/undo.py](xteink_service/undo.py) owns the table, the `record` / `apply` /
  `recent` functions, and the map from a `kind` string to the function that reverses it.
  `PLAN-2-READING-DATA.md` registers `progress_delete`, `progress_edit`, `book_delete`, and
  `writing_delete` against the same `undo_log` table and the same `POST /api/undo` endpoint. Two
  things follow. Do not add Plan 2's reversers here, and do not special-case any kind inside
  `apply()`: a new kind must be addable by registering a function from its own module, with no edit
  to `undo.py` at all. 1.3 names the `writing_delete` kind and fixes its payload shape, because 1.3
  owns the trash bucket and the toast label that have to agree with it, but the function that
  reverses it is registered from Plan 2's module like every other kind.
- **Placeholder covers from 1.5 are the fallback for real covers in `PLAN-3-POLISH.md` (3.2).**
  `PlaceholderCover.svelte` must take only a slug and render without any network access, so the
  cover component in Plan 3 can fall back to it when `cover_url` is absent or fails to load.
- **`search_docs.screenshot_id` is left NULL for clippings on purpose.** Persisting the
  clipping-to-screenshot link is `PLAN-2-READING-DATA.md` (2.6). Until then,
  [xteink_service/clip_match.py](xteink_service/clip_match.py) recomputes it per request in
  `_decorate()`, and search results for `kind='clipping'` link to the book page instead of a
  screenshot panel.

### Database boundary

There are two SQLite databases:

- `state.db` (env `STATE_DB`) holds the archive: `synced_screenshots`, `highlights`,
  `document_aliases`, `clippings`, `tbr_books`.
- `koreader.db` (env `KOREADER_DB`) holds `progress_updates` and `devices`.

[xteink_service/api.py](xteink_service/api.py) opens them with two separate helpers,
`_state_conn()` and `_koreader_conn()`, and combines the results in Python (see `_pages_by_hash()`
and `list_books()`).

**Every item in Plan 1 reads only `state.db`.** There is no `ATTACH DATABASE` anywhere in this
plan, and no new Python-side cross-database join either. The reason is that all six items operate
on passages (OCR text, highlights, clippings, notes), and passages live entirely in `state.db`.
`progress_updates` carries percentages, not text, so it has nothing to contribute to search,
export, trash, or the review queue. The one place a cross-database read might have been tempting
is the review queue showing "what you were reading at the time", and that is deliberately dropped
from scope.

If a later item genuinely needs both databases, the existing precedent is Python-side combination
(`list_books()` merges a `state.db` dictionary with a `koreader.db` result set on canonical slug).
Follow that, not `ATTACH`, because the two files can be replaced independently at runtime:
`ProgressStore._connect()` in [xteink_service/koreader_sync.py](xteink_service/koreader_sync.py)
opens a fresh connection per operation specifically so a deleted or replaced DB file is picked up
automatically, and an attached handle would defeat that.

### Schema evolution convention

There is no migration framework and none is being added. Schema evolution follows the two patterns
already in the codebase:

1. **Idempotent creation in a module init.** `SyncState._init_db()` in
   [xteink_service/state.py](xteink_service/state.py) and `init_db()` in
   [xteink_service/clippings.py](xteink_service/clippings.py) both issue
   `CREATE TABLE IF NOT EXISTS` on every call. `clippings.init_db()` is called at the top of each
   endpoint that touches the table. New modules follow the `clippings.py` shape.
2. **Column addition wrapped in try/except.** `SyncState._init_db()` loops over
   `[("vault_png_path", "TEXT DEFAULT ''"), ...]` issuing `ALTER TABLE ... ADD COLUMN` and swallows
   the failure when the column already exists. Narrow this to `sqlite3.OperationalError` in new
   code rather than bare `Exception`.

### Import-time DB path capture

[conftest.py](conftest.py) sets `KOREADER_DB` and `STATE_DB` to temp files at module scope, before
any test imports `xteink_service.koreader_sync`, because that module builds a module-level
`ProgressStore` that captures `KOREADER_DB` at import time.

**Every new module in this plan must read its DB path inside a function, never at import time.**
Concretely, each new module gets:

```python
def _state_db() -> str:
    return os.getenv("STATE_DB", "/data/state/state.db")
```

exactly as [xteink_service/clippings.py](xteink_service/clippings.py) and
[xteink_service/api.py](xteink_service/api.py) already do. No module-level `STATE_DB = os.getenv(...)`,
no module-level connection, no module-level singleton holding a path. If a future module cannot
avoid it, it must be added to the same `conftest.py` treatment, and that is a cost worth avoiding.

### Runtime and tooling constraints

- Runtime Python is **3.12** (`FROM python:3.12-slim` in [Dockerfile](Dockerfile)), but
  [pyproject.toml](pyproject.toml) declares `requires-python = ">=3.11"` and
  `[tool.ruff] target-version = "py311"`. Do not use 3.12-only syntax.
- **All database access is synchronous `sqlite3`.** `aiosqlite` is declared in
  [pyproject.toml](pyproject.toml) but is not imported anywhere in `xteink_service/`. Do not
  introduce async DB access. FastAPI endpoints declared `async def` that run short synchronous
  SQLite calls are the existing pattern throughout `api.py` and `clippings.py`; match it.
- `ruff` line-length is 100.
- **No new Python dependency.** Adding one means editing `pyproject.toml`, regenerating `uv.lock`,
  and rebuilding the Docker image, which is disproportionate for everything in this plan. Nothing
  here needs one.

### FTS5 availability: verify before writing any code

FTS5 is a compile-time option in SQLite. It is enabled by default in the SQLite amalgamation and in
Debian's `libsqlite3`, and CPython's `_sqlite3` module links against the system library in the
`python:3.12-slim` image, so FTS5 is **expected** to be available. This is an assumption, not a
fact, until it is checked. Run this before starting 1.1:

```bash
docker run --rm xteink-service:dev python -c "
import sqlite3
c = sqlite3.connect(':memory:')
c.execute(\"CREATE VIRTUAL TABLE t USING fts5(a, b, tokenize='unicode61 remove_diacritics 2')\")
c.execute(\"INSERT INTO t(a, b) VALUES ('Pastoral', 'the pastoral mode of reading')\")
row = c.execute(\"SELECT bm25(t, 10.0, 1.0), snippet(t, 1, '<mark>', '</mark>', '...', 8) FROM t WHERE t MATCH 'pastoral'\").fetchone()
print('sqlite', sqlite3.sqlite_version)
print('fts5 ok', row)
"
```

Expected output: a version string of at least `3.27.0` (required for the `remove_diacritics 2`
tokenizer option), followed by a negative bm25 score and a snippet containing `<mark>pastoral</mark>`.
If the `CREATE VIRTUAL TABLE` raises `sqlite3.OperationalError: no such module: fts5`, stop and
reassess item 1.1; everything else in this plan is unaffected.

---

## Work items

### 1.1 FTS5 search index

**Why.** `GET /api/search` in [xteink_service/api.py](xteink_service/api.py) runs five
`LIKE '%q%'` predicates (`s.book_title`, `s.ocr_text`, `s.ocr_corrected`, `s.user_notes`,
`h.selected_text`) joined with `GROUP_CONCAT`, which no index can serve: every row of
`synced_screenshots` is scanned and every OCR blob is compared. The whole schema has exactly one
index, `idx_clippings_doc_rev`. The endpoint also cannot see clippings at all, so an excerpt saved
on the device is unsearchable, and it hand-rolls a snippet by calling `text.lower().find(q_lower)`
in Python after loading every matching row's full OCR text into memory.

#### Data model

New module [xteink_service/search_index.py](xteink_service/search_index.py). All of this runs in
`state.db`.

```sql
-- Content table. Holds one row per indexable passage, from five sources.
CREATE TABLE IF NOT EXISTS search_docs (
    doc_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT    NOT NULL,              -- screenshot | note | highlight | clipping | writing
    ref_id        INTEGER NOT NULL,              -- id in the source table
    book_slug     TEXT    NOT NULL DEFAULT '',   -- canonical_book_slug(book_title)
    book_title    TEXT    NOT NULL DEFAULT '',
    body          TEXT    NOT NULL DEFAULT '',
    sort_date     TEXT    NOT NULL DEFAULT '',   -- YYYY-MM-DD
    screenshot_id INTEGER,                       -- NULL for kind='clipping' (see Plan 2, 2.6)
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kind, ref_id)
);

CREATE INDEX IF NOT EXISTS idx_search_docs_slug ON search_docs (book_slug);
CREATE INDEX IF NOT EXISTS idx_search_docs_kind ON search_docs (kind);

-- External-content FTS5 table. Stores only the inverted index; snippet() reads
-- column values back out of search_docs, which is why external content is used
-- instead of contentless (contentless tables cannot serve snippet()).
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    book_title,
    body,
    content='search_docs',
    content_rowid='doc_id',
    tokenize="unicode61 remove_diacritics 2"
);

-- The three documented external-content sync triggers.
CREATE TRIGGER IF NOT EXISTS search_docs_ai AFTER INSERT ON search_docs BEGIN
    INSERT INTO search_index(rowid, book_title, body)
    VALUES (new.doc_id, new.book_title, new.body);
END;

CREATE TRIGGER IF NOT EXISTS search_docs_ad AFTER DELETE ON search_docs BEGIN
    INSERT INTO search_index(search_index, rowid, book_title, body)
    VALUES ('delete', old.doc_id, old.book_title, old.body);
END;

CREATE TRIGGER IF NOT EXISTS search_docs_au AFTER UPDATE ON search_docs BEGIN
    INSERT INTO search_index(search_index, rowid, book_title, body)
    VALUES ('delete', old.doc_id, old.book_title, old.body);
    INSERT INTO search_index(rowid, book_title, body)
    VALUES (new.doc_id, new.book_title, new.body);
END;
```

Column order matters for ranking: `bm25(search_index, 10.0, 1.0)` weights `book_title` ten times
`body`. SQLite's `bm25()` returns a **negative** score where a more negative value is a better
match, so the ordering clause is `ORDER BY score ASC`, not `DESC`. Getting this backwards returns
the worst matches first and is the single easiest mistake to make here.

#### The fifth kind: writing entries

`PLAN-2-READING-DATA.md` item 2.5 adds a writing log: long-form entries about one book that cite
clippings, highlights, and screenshots inline. 1.1 does not design that feature. It owns three
things about it, and they have to be written now because retrofitting a kind into a built index
means a full reindex of every row.

**The kind.** `search_docs.kind` takes a fifth value, `writing`, whose `ref_id` is a
`writing_entries.id`, whose `book_slug` and `book_title` are the entry's book (so the `book` filter
and the facet chips behave exactly as they do for a passage), whose `sort_date` is
`writing_entries.entry_date`, and whose `screenshot_id` is always NULL. An entry is about a book,
not about one screenshot, even when it cites several.

**The anchor-expansion rule.** `writing_entries.body` is markdown containing inline anchor tokens
of the form `((ref:<writing_refs.id>))`. The indexed `body` is the entry body with every anchor
replaced by that ref's `writing_refs.snapshot`, which is the copy of the cited passage taken at
citation time. This is the behaviour a commonplace book wants: searching for a quoted phrase
returns both the clipping that holds it and the entry that cites it, and the two sit next to each
other in one ranked list. Indexing the raw body instead would make a citation invisible to search,
which is the opposite of the point. Three details are part of the rule, not implementation
latitude:

- An anchor whose id does not resolve to a row in `writing_refs`, or resolves to a ref belonging to
  a different entry, is replaced with the empty string, not left verbatim. The literal token
  tokenises to `ref` plus a number, so leaving it in would make every entry in the archive match a
  search for `ref`.
- A screenshot ref contributes its `snapshot` (the OCR text as it read at citation time) and
  nothing else. `image_path` is a path, not prose, and indexing it only produces matches on date
  fragments.
- `source_state` does not affect indexing. A ref whose source was deleted or has drifted still
  contributes its `snapshot`, because the snapshot is what the entry quoted and the entry still
  says it.

The expansion is one pure function, used at index time here, at export time in 1.2, and by the
vault render in 2.5:

```python
_ANCHOR_RE = re.compile(r"\(\(ref:(\d+)\)\)")

def expand_anchors(body: str, snapshots: dict[int, str]) -> str:
    """Replace every ((ref:N)) with snapshots[N], dropping ids that do not resolve."""
    return _ANCHOR_RE.sub(lambda m: snapshots.get(int(m.group(1)), ""), body)
```

It lives in [xteink_service/search_index.py](xteink_service/search_index.py) because 1.1 lands
first and the function is pure, stdlib-only, and takes no connection. 1.2 and 2.5 import it from
there rather than reimplementing it. Three copies of a substitution rule is three renders that
drift, and the drift is silent: the search index and the exported file would quietly disagree about
what an entry says. If a fourth caller appears, move it to its own module then, not now.

**The weighting.** A writing entry's own `writing_entries.title` is prepended to the indexed
`body`, separated by a blank line, and is **not** given title weight. `book_title` keeps holding
the book's title for every kind including this one. The 10x weight on `book_title` exists so that
searching for a book's name returns that book's material ahead of material that merely mentions it,
and an entry title is not that: it is one line of user prose sitting at the same level as the rest
of the entry. Weighting it ten times would float an entry called "On attention" above a clipping
that actually contains the word, which inverts the ranking the user asked for. The alternative,
a third FTS5 column for the entry title, changes the `bm25()` arity in the search and facet
queries, changes all three sync triggers, and forces a full rebuild of an index whose other four
kinds have no such column. That is a large change to buy a ranking the archive does not want.

Upsert for a single document:

```sql
INSERT INTO search_docs
       (kind, ref_id, book_slug, book_title, body, sort_date, screenshot_id)
VALUES (?,    ?,      ?,         ?,          ?,    ?,         ?)
ON CONFLICT(kind, ref_id) DO UPDATE SET
    book_slug     = excluded.book_slug,
    book_title    = excluded.book_title,
    body          = excluded.body,
    sort_date     = excluded.sort_date,
    screenshot_id = excluded.screenshot_id,
    updated_at    = CURRENT_TIMESTAMP;
```

Removal for a single document:

```sql
DELETE FROM search_docs WHERE kind = ? AND ref_id = ?;
```

The search query:

```sql
SELECT d.kind, d.ref_id, d.book_slug, d.book_title, d.sort_date, d.screenshot_id,
       snippet(search_index, 1, '<mark>', '</mark>', '...', 24) AS snippet,
       bm25(search_index, 10.0, 1.0)                            AS score
FROM search_index
JOIN search_docs d ON d.doc_id = search_index.rowid
WHERE search_index MATCH ?
  AND (:kinds_csv = '' OR d.kind IN (SELECT value FROM json_each(:kinds_json)))
  AND (:book_slug = '' OR d.book_slug = :book_slug)
ORDER BY score
LIMIT :limit OFFSET :offset;
```

`json_each` is built into SQLite, needs no extension, and avoids string-built `IN` lists. If the
JSON1 check in the verification command above fails, fall back to the placeholder-building pattern
already used in `api._hashes_for_book()` (`",".join("?" for _ in hashes)`).

The facet query runs the same `MATCH` once more:

```sql
SELECT d.kind, COUNT(*) AS n
FROM search_index
JOIN search_docs d ON d.doc_id = search_index.rowid
WHERE search_index MATCH ?
  AND (:book_slug = '' OR d.book_slug = :book_slug)
GROUP BY d.kind;
```

Facet counts deliberately ignore the `kinds` filter so the chips keep showing the other kinds'
counts while one kind is selected.

#### API

`GET /api/search`

| Param | Type | Default | Notes |
|---|---|---|---|
| `q` | str | `""` | Free text. Empty returns an empty result set with zeroed facets. |
| `kinds` | str | `""` | Comma-separated subset of `screenshot,note,highlight,clipping,writing`. Empty means all. Unknown values are ignored. |
| `book` | str | `""` | A canonical book slug. Filters to one book. |
| `limit` | int | `50` | `Query(default=50, ge=1, le=200)` |
| `offset` | int | `0` | `Query(default=0, ge=0)` |
| `notes_only` | bool | `false` | Deprecated alias. When true and `kinds` is empty, behaves as `kinds=note` and `q` may be empty (matching today's behaviour, which lists every screenshot with a note). |

Response:

```json
{
  "q": "pastoral",
  "kinds": ["screenshot", "note"],
  "book": "",
  "limit": 50,
  "offset": 0,
  "total": 42,
  "facets": { "screenshot": 30, "note": 2, "highlight": 6, "clipping": 4, "writing": 0 },
  "results": [
    {
      "kind": "screenshot",
      "ref_id": 41,
      "screenshot_id": 41,
      "book_title": "Pastoral",
      "book_slug": "pastoral",
      "date": "2026-07-04",
      "snippet": "...the <mark>pastoral</mark> mode of attention...",
      "score": -8.4213
    }
  ]
}
```

`facets` always contains all five keys, zero-filled, including `writing` on every database built
before 2.5 lands. `total` is the sum of `facets` values
restricted by the `book` filter, not the length of `results`. `snippet` contains `<mark>` tags
produced by SQLite and is the only field the client renders as HTML. **The client must escape
nothing else and must not re-run its own highlighting**; the existing
`highlight()`/`escape()` pair in
[web/src/routes/search/+page.svelte](web/src/routes/search/+page.svelte) is deleted. Because
`snippet()` emits the stored text verbatim between the tags, the server must HTML-escape
`&`, `<`, `>` in `body` before indexing, or escape the snippet's non-tag content on the way out.
Choose the second: escape at index time would corrupt the export in 1.2 and the `body` column is
shared. Implement `_escape_snippet()` in `search_index.py` that splits on the literal
`<mark>`/`</mark>` delimiters, escapes each segment, and rejoins.

`POST /api/search/reindex`

Request body: none. Response:

```json
{
  "indexed": { "screenshot": 1240, "note": 31, "highlight": 88, "clipping": 212, "writing": 0 },
  "skipped": ["writing"],
  "duration_ms": 2841
}
```

`skipped` lists source kinds whose table did not exist (see "Backfill" below). Until 2.5 lands,
`writing` is always in `skipped`, which is the correct and visible answer rather than a silent
zero.

#### Service changes

New file [xteink_service/search_index.py](xteink_service/search_index.py):

- `_state_db() -> str` reading `STATE_DB` inside the function.
- `init_db(conn: sqlite3.Connection) -> None` issuing the DDL above. Idempotent.
- `_register_slug_fn(conn)` calling
  `conn.create_function("canon_slug", 1, canonical_book_slug, deterministic=True)` so the
  set-based rebuild can compute `book_slug` in SQL.
- `to_match_query(q: str) -> str | None` turning raw user input into a safe FTS5 query.
  Implementation: split on whitespace, strip every character that is not a unicode word character
  or an apostrophe from each token, drop empties, wrap each surviving token in double quotes,
  join with a space (implicit AND), and append `*` to the final token for prefix matching. Return
  `None` when nothing survives, and have the caller return an empty result set. This is what
  prevents `sqlite3.OperationalError: fts5: syntax error` on input like `"` or `foo AND` or `*`.
- `index_screenshot(conn, screenshot_id)`, `index_note(conn, screenshot_id)`,
  `index_highlight(conn, highlight_id)`, `index_clipping(conn, clipping_id)`. Each reads the row,
  computes `book_slug`, and runs the upsert. `index_note` deletes the `note` doc when
  `user_notes` is empty rather than indexing a blank body.
- `expand_anchors(body, snapshots)` as given above, and
  `index_writing(conn, entry_id)`, which reads the entry, reads its refs, expands the anchors, and
  runs the same upsert. It deletes the `writing` doc instead when the entry is soft-deleted
  (`deleted_at IS NOT NULL`), so a trashed entry leaves search the same way a trashed highlight
  does. It raises nothing when `writing_entries` does not exist; see the freshness rules below.
- `index_writing_for_ref(conn, ref_id)`, resolving
  `SELECT entry_id FROM writing_refs WHERE id = ?` and calling `index_writing` on that one entry.
  A citation belongs to exactly one entry, so a ref change is never a multi-row reindex.
- `unindex(conn, kind: str, ref_id: int) -> None`.
- `rebuild(db_path: str) -> dict` running the four `INSERT ... SELECT` statements below plus the
  writing pass inside one `BEGIN IMMEDIATE`, each wrapped in
  `try/except sqlite3.OperationalError` so a missing source table is recorded in `skipped` rather
  than aborting.
- `ensure_built(db_path: str) -> dict | None` returning `None` when `search_docs` already has rows,
  otherwise calling `rebuild`.
- `search(db_path, q, kinds, book, limit, offset) -> dict` returning the response shape above.

The rebuild statements:

```sql
DELETE FROM search_docs;

INSERT INTO search_docs (kind, ref_id, book_slug, book_title, body, sort_date, screenshot_id)
SELECT 'screenshot', s.id, canon_slug(s.book_title), COALESCE(s.book_title, ''),
       COALESCE(NULLIF(s.ocr_corrected, ''), s.ocr_text, ''),
       COALESCE(s.sync_date, ''), s.id
FROM synced_screenshots s
WHERE COALESCE(NULLIF(s.ocr_corrected, ''), s.ocr_text, '') != ''
   OR COALESCE(s.book_title, '') != '';

INSERT INTO search_docs (kind, ref_id, book_slug, book_title, body, sort_date, screenshot_id)
SELECT 'note', s.id, canon_slug(s.book_title), COALESCE(s.book_title, ''),
       s.user_notes, COALESCE(s.sync_date, ''), s.id
FROM synced_screenshots s
WHERE s.user_notes IS NOT NULL AND s.user_notes != '';

INSERT INTO search_docs (kind, ref_id, book_slug, book_title, body, sort_date, screenshot_id)
SELECT 'highlight', h.id, canon_slug(s.book_title), COALESCE(s.book_title, ''),
       h.selected_text, COALESCE(s.sync_date, ''), h.screenshot_id
FROM highlights h
JOIN synced_screenshots s ON s.id = h.screenshot_id
WHERE h.selected_text != '';
-- After 1.3 lands, append: AND h.deleted_at IS NULL

INSERT INTO search_docs (kind, ref_id, book_slug, book_title, body, sort_date, screenshot_id)
SELECT 'clipping', c.id, canon_slug(a.title), COALESCE(a.title, ''), c.text,
       CASE WHEN c.created_at > 0
            THEN date(c.created_at, 'unixepoch')
            ELSE substr(COALESCE(c.synced_at, ''), 1, 10) END,
       NULL
FROM clippings c
LEFT JOIN document_aliases a ON a.hash = c.document
WHERE c.deleted = 0 AND c.text != '';

INSERT INTO search_index(search_index) VALUES('rebuild');
```

The final `'rebuild'` command regenerates the whole inverted index from the content table, which
makes the per-row trigger work during the bulk insert redundant but harmless, and guarantees the
index and content table agree even if a trigger was missing on an older database.

**Writing entries are rebuilt in Python, not by a fifth `INSERT ... SELECT`.** SQLite has no regex
and no repeated-substitution primitive, so expanding `((ref:N))` in SQL means either a recursive
CTE that walks an entry's refs one `replace()` at a time or a scalar function that cannot see the
refs. Both are considerably harder to read than a loop, and the loop is cheap: writing entries are
hand-written long-form prose, so the archive has tens or hundreds of them against tens of thousands
of OCR blobs. The pass is two queries and the upsert:

```sql
SELECT e.id, e.book_slug, e.entry_date, e.title, e.body
FROM writing_entries e
WHERE e.deleted_at IS NULL;

SELECT r.entry_id, r.id, r.snapshot
FROM writing_refs r
JOIN writing_entries e ON e.id = r.entry_id
WHERE e.deleted_at IS NULL;
```

Read both in full, group the second by `entry_id` into a `dict[int, str]` of snapshots, and upsert
one `search_docs` row per entry with
`body = title + "\n\n" + expand_anchors(entry_body, snapshots)`. The book title comes from the same
resolution the other kinds use, keyed on `book_slug`. Soft-deleted entries are excluded by the
`WHERE`, so a trashed entry is absent from the rebuilt index with no second statement.

#### Keeping a writing row fresh

The three triggers above keep `search_index` in step with `search_docs`. Nothing keeps
`search_docs` in step with `writing_entries`, and **no trigger can**: the expansion is a regex
substitution over a variable number of refs, which SQLite cannot express in a trigger body. Do not
add one and do not approximate one. The freshness rules are explicit calls, exactly like the other
four kinds:

| Change | Call | Why |
|---|---|---|
| Entry created or its `title` / `body` edited | `index_writing(conn, entry_id)` | The indexed text is derived from both columns. |
| Entry soft-deleted | `index_writing(conn, entry_id)` | It deletes the doc, because the entry is in the trash. |
| Entry restored from `/trash` | `index_writing(conn, entry_id)` | Same call, opposite outcome. One function, no branch at the call site. |
| A ref added, removed, or its `snapshot` rewritten | `index_writing_for_ref(conn, ref_id)` | The snapshot is part of the indexed body. |
| A ref's `source_state` flipped to `deleted` or `drifted` | **nothing** | `source_state` is not indexed and the flip does not touch `snapshot`, so the indexed text is byte-identical. Re-indexing here would be work that changes no row. |

The last row is the one worth stating out loud, because 1.3's clipping and highlight deletes flip
citations to `source_state='deleted'` and it would be reasonable to assume that needs a reindex. It
does not, and that is the point of the snapshot: deleting a cited clipping does not change a single
word of what the entry says or of what search finds in it.

Those call sites are Plan 2's to write, because 2.5 owns every write path into `writing_entries`
and `writing_refs`. 1.1 owns the functions and this table. Until 2.5 lands there is no caller, the
rebuild records `writing` in `skipped`, and `GET /api/search` returns `"writing": 0` in its facets.

Modified call sites, each adding one indexing call inside the existing transaction:

| File | Function | Change |
|---|---|---|
| [xteink_service/state.py](xteink_service/state.py) | `SyncState._init_db` | Call `search_index.init_db(conn)` after the existing `CREATE TABLE` block. |
| [xteink_service/state.py](xteink_service/state.py) | `SyncState.mark_synced` | After the `INSERT OR IGNORE`, call `search_index.index_screenshot(conn, rowid)`. Resolve the rowid with `SELECT id FROM synced_screenshots WHERE device_path=? AND content_hash=?` since `INSERT OR IGNORE` yields no `lastrowid` on conflict. |
| [xteink_service/state.py](xteink_service/state.py) | `SyncState.update_screenshot` | After the `UPDATE`, call `index_screenshot` and `index_note`. |
| [xteink_service/state.py](xteink_service/state.py) | `SyncState.add_highlight` | After the `INSERT`, call `index_highlight(conn, cur.lastrowid)`. |
| [xteink_service/state.py](xteink_service/state.py) | `SyncState.delete_highlight` | Call `unindex(conn, "highlight", highlight_id)`. (1.3 changes this to a soft delete; the unindex call stays identical.) |
| [xteink_service/api.py](xteink_service/api.py) | `update_screenshot` | Same two calls as `SyncState.update_screenshot`. This endpoint writes with a raw `sqlite3.connect(_state_db())` rather than through `SyncState`, so it needs its own call. |
| [xteink_service/clippings.py](xteink_service/clippings.py) | `_apply` | After the upsert, `index_clipping(conn, rowid)` for a live item, `unindex(conn, "clipping", rowid)` for a tombstone. Resolve the rowid with `SELECT id FROM clippings WHERE document=? AND clip_id=?`. |
| [xteink_service/clippings.py](xteink_service/clippings.py) | `delete_clipping` | `unindex(conn, "clipping", clipping_id)`. |
| [xteink_service/api.py](xteink_service/api.py) | `search` | Replace the entire body with a call to `search_index.search(...)`. Delete the `LIKE` block, the `match_fields` loop, and the Python snippet extraction. `_dedup_by_image()` no longer applies, because results are per passage, not per screenshot row; stitched screenshots share a `vault_png_path` but have distinct ids and distinct OCR text, so they are legitimately distinct passages. |
| [xteink_service/api.py](xteink_service/api.py) | new `reindex_search` | `POST /api/search/reindex`. |
| [xteink_service/main.py](xteink_service/main.py) | `main()` | Before `await asyncio.gather(...)`, call `await asyncio.to_thread(search_index.ensure_built, state_db)` and log the result. Do NOT use a FastAPI startup or lifespan hook: `tests/test_api.py` and `tests/test_clippings.py` construct `TestClient(app)` directly, and a startup hook would run a full rebuild in every test session. |

`_PUBLIC_ALLOWED` in [xteink_service/koreader_sync.py](xteink_service/koreader_sync.py) is
`("/syncs", "/users", "/health", "/api/v1/clippings")`. **`/api/search` and `/api/search/reindex`
must not be added to it.** They are UI routes and belong on the private host only. The same applies
to every new route in this plan: 1.2's export, 1.3's trash and restore, and 1.4's on-this-day and
review endpoints all stay off the allowlist. The only device-facing paths are the kosync endpoints
and `/api/v1/clippings`, and nothing in Plan 1 changes either.

#### UI

- [web/src/lib/api.ts](web/src/lib/api.ts): replace `SearchResult` with:

```ts
export type SearchKind = 'screenshot' | 'note' | 'highlight' | 'clipping' | 'writing';

export interface SearchHit {
  kind: SearchKind;
  ref_id: number;
  screenshot_id: number | null;
  book_title: string;
  book_slug: string;
  date: string;
  snippet: string;
  score: number;
}

export interface SearchResponse {
  q: string;
  kinds: SearchKind[];
  book: string;
  limit: number;
  offset: number;
  total: number;
  facets: Record<SearchKind, number>;
  results: SearchHit[];
}
```

  and change the wrapper to
  `search: (q, opts?: { kinds?: SearchKind[]; book?: string; limit?: number; offset?: number }) => get<SearchResponse>(...)`.
  Add `reindexSearch: () => post<{...}>('/api/search/reindex', {})`.
- [web/src/routes/search/+page.ts](web/src/routes/search/+page.ts): read `kinds` and `book` from
  the URL alongside `q`, keep accepting `notes_only=1` and translate it to `kinds=note`.
- [web/src/routes/search/+page.svelte](web/src/routes/search/+page.svelte): delete the local
  `highlight()`/`escape()` helpers and the `FIELD_LABELS` map. Render `{@html hit.snippet}`.
  Add a facet chip row above the results that toggles `kinds` in the URL. Render a thumbnail only
  when `screenshot_id` is non-null; otherwise render a book link. A `writing` hit always takes the
  book-link branch; the deep link into a single entry is 2.5's to define, and until 2.5 lands no
  writing hit is ever returned, so nothing here renders differently.
- New [web/src/lib/components/FacetChips.svelte](web/src/lib/components/FacetChips.svelte):
  props `{ facets: Record<SearchKind, number>; selected: SearchKind[]; onToggle: (k: SearchKind) => void }`.

#### Backfill / migration

- `search_docs` and `search_index` are created by `init_db()` on the first call, so an existing
  `state.db` gains them with no manual step.
- Backfill happens in one of three ways, all calling the same `rebuild()`: automatically at service
  start via `ensure_built()` (only when `search_docs` is empty), manually via
  `POST /api/search/reindex`, or from the command line.
- `rebuild()` is **idempotent and re-runnable**: it opens `BEGIN IMMEDIATE`, truncates
  `search_docs`, re-derives every row from the source tables, and runs the FTS `'rebuild'` command.
  Running it twice produces byte-identical content, and running it concurrently with a device sync
  is safe because `BEGIN IMMEDIATE` takes the write lock.
- A `state.db` whose `highlights` or `clippings` table does not exist yet (a fresh install, or the
  hand-built fixture DB in `tests/test_api.py`) records that kind in `skipped` and indexes the
  rest.
- `writing_entries` and `writing_refs` do not exist on any database until 2.5 lands, so `writing`
  is reported in `skipped` on every reindex until then. **1.1 must not create those two tables.**
  Creating them empty here would hide the real seam, would fix their columns before 2.5 has
  finished designing them, and would turn an honest `skipped` entry into a permanent zero that
  looks like a working indexer. The one reindex after 2.5 lands is what fills the kind in.

#### Risks

- **Index size.** `search_docs.body` duplicates every OCR blob, so `state.db` roughly doubles in
  size, and the FTS index adds perhaps another 30 to 50 percent of the text size on top. For an
  archive measured in thousands of screenshots this is tens of megabytes, which is acceptable, but
  it should be measured on the real database before and after the first reindex.
- **Query syntax errors.** Unescaped user input reaching `MATCH` raises `OperationalError` and
  returns a 500. `to_match_query()` is the only defence and must be unit tested against `"`, `*`,
  `^`, `NEAR(`, `AND`, and an all-punctuation query.
- **Snippet escaping.** Getting `_escape_snippet()` wrong is an XSS hole, because the client
  renders the snippet with `{@html}`. OCR text comes from images of books, so hostile input is
  unlikely, but `user_notes` is user-authored and the clipping `text` arrives over the network.
- **Trigger drift.** If a write path is added later that bypasses `search_index`, the index goes
  stale silently. `POST /api/search/reindex` is the recovery, and the reindex button in the UI is
  the reason it exists.
- **Writing entries have no trigger to drift from.** The other four kinds at least have a shape a
  trigger could have taken; the writing row cannot, so its freshness rests entirely on 2.5 calling
  `index_writing` and `index_writing_for_ref` on every write path. A missed call means an entry
  that is edited but still searchable under its old wording, which is the quietest failure in this
  item. The mitigation is the same reindex button, and the reason the freshness table above is
  written as a contract rather than as advice.
- **Breaking response shape.** `/api/search` changes from an array to an object. Anything holding
  the old shape (the search page, `web/src/lib/api.test.ts`) breaks loudly, which is the intent.

---

### 1.2 Export a book's passages

**Why.** The vault is the only export path today. [xteink_service/vault_writer.py](xteink_service/vault_writer.py)
writes `Books/<title>.md` on sync, but there is no way to pull one book's highlights and clippings
out of the web UI, so sharing a book's passages means opening Obsidian and copying by hand. The
data is already assembled per book by `list_book_highlights()` in
[xteink_service/api.py](xteink_service/api.py) and `book_clippings()` in
[xteink_service/clippings.py](xteink_service/clippings.py); only the serialisation is missing.

#### Data model

**No schema change.** This item is read-only.

#### API

`GET /api/books/{slug}/export`

| Param | Type | Default | Notes |
|---|---|---|---|
| `format` | str | `md` | One of `md`, `json`, `csv`. Anything else returns 400. |
| `include` | str | `highlights,clippings,notes,writing` | Comma-separated subset of `highlights`, `clippings`, `notes`, `writing`. Unknown values return 400 so a typo does not silently export less than expected. |
| `group` | str | `auto` | One of `auto`, `chapter`, `date`. `auto` picks `chapter` when at least one included clipping has a non-empty `chapter`, otherwise `date`. Writing entries never influence the `auto` decision; see "Where writing entries sit" below. |

`writing` is in the default `include` set. An export that silently omitted the user's own writing
would be the one omission they noticed, and `include` exists to narrow an export, not to discover
that half of it was missing. A caller that wants only passages asks for
`include=highlights,clippings,notes`.

Responses:

- `format=md`: `text/markdown; charset=utf-8`, `Content-Disposition: attachment; filename="<slug>.md"`.
- `format=csv`: `text/csv; charset=utf-8`, same disposition with `.csv`. Header row:
  `kind,book_title,group,date,chapter,page_label,text,note,source_id,screenshot_id`.
- `format=json`: `application/json`, no disposition (the UI copies it rather than downloading it).

#### Where writing entries sit

A writing entry from `PLAN-2-READING-DATA.md` item 2.5 is long-form prose about one book that
cites clippings, highlights, and screenshots inline. It has an `entry_date` and a `position_pct`,
but no chapter, so the grouping rules need one decision each:

- `group=date`: an entry's group key is its `entry_date`, the same key a passage captured that day
  gets. An entry and the passages it was written about land together, which is the whole reason
  date grouping exists.
- `group=chapter`: entries do not fall into `"Unchaptered"`. They go into a single group labelled
  `"Writing"`, emitted **first**, before every chapter group. An entry is about the book rather
  than about one chapter, and `position_pct` is a reading position, not a chapter identity.
  Burying a page of the user's own prose under `"Unchaptered"` at the bottom of a file, below two
  hundred clippings, would be the wrong answer to a question nobody asked.
- `group=auto` is decided by clippings alone, unchanged. A book with writing but no chaptered
  clippings still groups by date, which is right: the chapters are not there to group by.

Within a group, writing entries come before highlights, clippings, and notes, ordered by
`entry_date` then `id`. An entry quotes the passages around it, so reading it first and the raw
passages after matches both the vault render and the order a person would read the page in. The
reverse puts the commentary after the material it is commenting on, where it reads as an orphan.

#### Expanding a citation

An exported entry expands its anchors, exactly as the vault render in 2.5 does, using the same
`expand_anchors()` from [xteink_service/search_index.py](xteink_service/search_index.py) that 1.1
defines. A file full of unresolvable `((ref:31))` tokens is not an export; it is a database dump
with the database missing. The same rule as 1.1 applies to an id that does not resolve or belongs
to another entry: it is dropped, not printed.

The cited text always comes from `writing_refs.snapshot`, never from a live re-read of the source
row. That is what makes the export stable: a clipping deleted last week, or re-keyed by a device
match-method switch, still exports the words the entry actually quoted. A ref whose
`source_state` is `deleted` or `drifted` therefore exports its snapshot like any other, with the
state noted so the reader knows the source is no longer there to check against.

Per ref kind, per format:

| `writing_refs.kind` | `format=md` | `format=json` | `format=csv` |
|---|---|---|---|
| `clipping` | the snapshot as a blockquote, then an attribution line | a `refs[]` entry, anchors preserved in `body` | inlined into `text`, structure lost |
| `highlight` | the snapshot as a blockquote, then an attribution line | same | same |
| `screenshot` | an `![[image_path]]` embed, then the snapshot as a blockquote caption | same, with `image_path` populated | the snapshot only; the embed is dropped |

Markdown, with the entry title as an `###` heading under its group heading:

```markdown
## Writing

### On attention

`writing` 41% - 2026-11-07

What stayed with me was this:

> A sentence worth keeping.

`cited clipping` ch. page 11 of 1 - 2026-07-04

and the page it sat on:

![[Pastoral/2026-07-04-01.png]]

> Apple. Now, because Bigland did not get home...

`cited screenshot` 2026-07-04 - source deleted
```

The screenshot embed uses the Obsidian `![[...]]` wikilink form because the vault already writes
screenshots that way and the manual check for this item pastes the export into Obsidian. Be clear
about the cost: outside the vault that embed resolves to nothing. The snapshot blockquote under it
is there for exactly that case, so a file read anywhere still carries the words even when the image
is gone.

The attribution line follows the existing passage format, `` `kind` <page_label> - <date> ``, with
`cited ` prefixing the kind so a quotation inside an entry is never mistaken for a passage of its
own. A non-`live` ref appends ` - source deleted` or ` - source changed since citation`. Those two
strings are part of the contract, like 1.3's toast labels: a reader who finds a quotation they
cannot locate in the book needs to be told why, not left to guess.

JSON shape:

```json
{
  "book_slug": "the-hidden-keys",
  "book_title": "The-Hidden-Keys",
  "generated_at": "2026-11-07T09:14:02+00:00",
  "include": ["highlights", "clippings", "notes", "writing"],
  "grouped_by": "chapter",
  "counts": { "highlights": 12, "clippings": 31, "notes": 3, "writing": 2 },
  "groups": [
    {
      "label": "Writing",
      "items": [
        {
          "kind": "writing",
          "id": 12,
          "title": "On attention",
          "body": "What stayed with me was this:\n\n((ref:31))\n",
          "body_expanded": "What stayed with me was this:\n\nA sentence worth keeping.\n",
          "date": "2026-11-07",
          "position_pct": 41.5,
          "refs": [
            {
              "anchor": "((ref:31))",
              "id": 31,
              "kind": "clipping",
              "ref_id": 184,
              "document": "aaaaaaaa...",
              "snapshot": "A sentence worth keeping.",
              "image_path": null,
              "position": "ch. page 11 of 1",
              "source_state": "deleted"
            }
          ]
        }
      ]
    },
    {
      "label": "Chapter One",
      "items": [
        {
          "kind": "clipping",
          "id": 184,
          "text": "A sentence worth keeping.",
          "chapter": "Chapter One",
          "date": "2026-07-04",
          "page_label": "ch. page 11 of 1",
          "note": null,
          "screenshot_id": null
        }
      ]
    }
  ]
}
```

JSON carries **both** `body` with its anchors intact and `body_expanded` with them resolved, plus
the refs as a structured array. This is deliberate duplication. JSON is the format a re-import or a
script would read, and dropping the anchors would make a round trip lossy, while dropping the
expansion would make every consumer reimplement the substitution and get the unresolved-id case
wrong. `refs` is ordered by first appearance of the anchor in `body`, so a consumer that wants to
rebuild the rendering can walk it in order. `image_path` is non-null only for a `screenshot` ref,
and `document` only for a `clipping`.

Markdown shape:

```markdown
# The-Hidden-Keys

> Exported 2026-11-07. 12 highlights, 31 clippings, 3 notes, 2 writing entries.

## Chapter One

> A sentence worth keeping.

`clipping` ch. page 11 of 1 - 2026-07-04

> Another passage, highlighted on a screenshot.

`highlight` 2026-07-04
```

Notes are emitted under the passage they belong to, as a trailing line prefixed with `Note:`, not
as a separate blockquote, because a note is always attached to one screenshot.

**The exported Markdown contains no `((ref:` token anywhere.** That is the test, and it is the
whole reason the expansion exists.

CSV is the lossy format and the plan says so rather than pretending otherwise. The header row is
unchanged. A writing entry becomes one row: `kind=writing`, `chapter` empty, `date` the
`entry_date`, `page_label` the position as a whole-number percentage (`41%`), `source_id` the entry
id, `screenshot_id` empty, `note` empty, and `text` the entry title and the expanded body joined by
a blank line. What is lost: where each quotation started and ended, which kind of source each one
came from, every `source_state`, and every screenshot `image_path`. A cited clipping also appears
twice in the file, once as its own row and once inside the entry's `text`, because CSV has no
nesting to express "this row quotes that one". Do not add columns to carry any of it. CSV is the
spreadsheet format; `json` and `md` are the structured ones, and a user who needs the citations
intact should be told to use those.

#### Service changes

New file [xteink_service/export.py](xteink_service/export.py):

- `_state_db()` reading the env var inside the function.
- `collect(db_path: str, slug: str, include: set[str]) -> dict` returning
  `{"book_title": str, "highlights": [...], "clippings": [...], "notes": [...], "writing": [...]}`.
  It reuses `canonical_book_slug` for matching and mirrors the existing queries:
  the highlight query from `api.list_book_highlights()` (joined to `synced_screenshots`), the
  clipping query from `clippings.book_clippings()` (every `document` hash whose alias resolves to
  the slug, `deleted = 0`), and a notes query over
  `synced_screenshots WHERE user_notes IS NOT NULL AND user_notes != ''` restricted by
  `_book_titles_for_slug`-style canonical matching.
  After 1.3 lands, the highlight query gains `AND h.deleted_at IS NULL`.
  The writing collection is two queries, keyed directly on the slug because 2.5 stores it:

```sql
SELECT id, entry_date, title, body, position_pct
FROM writing_entries
WHERE book_slug = :slug AND deleted_at IS NULL
ORDER BY entry_date, id;

SELECT id, entry_id, kind, ref_id, document, snapshot, image_path, position, source_state
FROM writing_refs
WHERE entry_id IN (SELECT id FROM writing_entries
                   WHERE book_slug = :slug AND deleted_at IS NULL)
ORDER BY entry_id, id;
```

  Both are wrapped in `try/except sqlite3.OperationalError`, which yields an empty `writing` list
  on every database until 2.5 lands. That is the same degradation the search rebuild uses, and it
  is what lets `writing` sit in the default `include` set from the day 1.2 ships without turning
  every export into a 500.
- `expand_entry(entry: dict, refs: list[dict], fmt: str) -> str` producing the rendered body for
  one entry. It builds the `{ref_id: replacement}` mapping for the format (`md` uses the
  blockquote and embed forms above, `json` and `csv` use the bare snapshot) and calls
  `search_index.expand_anchors()`. The import from `search_index` is intentional and is the one
  coupling in this item: one implementation, three callers, because a second copy of the rule would
  let the export and the search index disagree about what an entry says, silently.
- `group_items(collected: dict, mode: str) -> tuple[str, list[dict]]` returning the chosen mode and
  the grouped list. Group key per item: the item's `chapter` when mode is `chapter` and the item
  has a non-empty chapter, the literal `"Unchaptered"` when mode is `chapter` and it does not, and
  the item's date when mode is `date`. A writing entry is the exception: in `chapter` mode its key
  is the literal `"Writing"` and that group is emitted first, never `"Unchaptered"`. Chapter groups
  are ordered by first appearance (clippings
  come back ordered by `spine, start_page, start_word`, which is reading order); date groups are
  ordered newest first, matching the rest of the UI. Inside a group, writing entries sort before
  passages.
- `render_md(payload: dict) -> str`, `render_csv(payload: dict) -> str` (via `csv.writer` into an
  `io.StringIO`), `render_json(payload: dict) -> dict`.
- `build(db_path, slug, include, fmt, group) -> tuple[str | dict, str, str]` returning
  `(body, media_type, filename)`. Keeping this a pure function with no FastAPI import is what makes
  it testable without a `TestClient`.

[xteink_service/api.py](xteink_service/api.py) gains one endpoint, `export_book`, that validates
`format`/`include`/`group`, calls `export.build`, and returns a `Response` (not `FileResponse`,
since nothing is written to disk) with the right media type and `Content-Disposition`. Return 404
when `collect()` finds no book title and all three collections are empty.

#### UI

- [web/src/lib/api.ts](web/src/lib/api.ts): add

```ts
books: {
  exportUrl: (slug: string, opts: { format: 'md' | 'json' | 'csv'; include: string[]; group?: string }) =>
    `/api/books/${encodeURIComponent(slug)}/export?` +
    new URLSearchParams({ format: opts.format, include: opts.include.join(','), group: opts.group ?? 'auto' }),
  exportJson: (slug: string, include: string[]) => get<BookExport>(...),
}
```

  Downloads use a plain anchor pointing at `exportUrl` so the browser handles
  `Content-Disposition`; only the "copy as JSON" action goes through `exportJson`.
- New [web/src/lib/components/ExportMenu.svelte](web/src/lib/components/ExportMenu.svelte):
  props `{ slug: string; defaultInclude?: string[] }`. A button plus a popover with four
  checkboxes (highlights, clippings, notes, writing), three format buttons, and a "copy JSON"
  action. Close
  on outside click and on Escape. No new dependency; use a `<details>` element or a
  `document.addEventListener('click', ...)` in `$effect` with cleanup.
- Mount points:
  - [web/src/routes/books/[book]/+page.svelte](web/src/routes/books/%5Bbook%5D/+page.svelte):
    in the title row, next to the `{visibleCount} / {screenshots.length}` counter.
  - [web/src/routes/clippings/+page.svelte](web/src/routes/clippings/+page.svelte): one menu per
    book section heading, with `defaultInclude={['clippings']}`.
  - [web/src/routes/highlights/+page.svelte](web/src/routes/highlights/+page.svelte): same, with
    `defaultInclude={['highlights']}`.

#### Backfill / migration

None. No tables, no columns, no state. The endpoint derives everything from existing rows on each
call, so it is trivially idempotent and re-runnable.

#### Risks

- **Slug ambiguity.** `canonical_book_slug()` collapses `"The-Hidden-Keys"` and
  `"Hidden Keys, The - Andre Alexis"` to one slug, which is the point, but it also strips a
  trailing `" - Author"` from any title containing `" - "`. Two genuinely different books whose
  titles collide after canonicalisation would export together. This is a pre-existing property of
  the key, not something this item introduces.
- **Large exports.** A book with thousands of clippings produces a multi-megabyte string built in
  memory. Acceptable for one book; do not generalise this endpoint to "export everything" without
  streaming.
- **CSV and Excel.** Clipping text contains newlines. `csv.writer` quotes them correctly;
  hand-rolling the CSV would not. Use the stdlib.
- **An export is a snapshot of snapshots.** A cited passage is exported from
  `writing_refs.snapshot`, which was taken when the citation was made, not from the source row as
  it reads today. If a user corrects a clipping's OCR after citing it, the export shows the old
  wording with no indication that it changed, unless 2.5 flipped the ref to `drifted`. That is the
  right trade, because the alternative is an export whose quotations do not match the prose that
  introduces them, but it is a real property and the ` - source changed since citation` marker is
  the only thing that surfaces it.
- **`writing` in the default `include` changes what a bare export URL returns** the day 2.5 lands,
  with no change to 1.2. That is intended. It is worth knowing that the first export after 2.5 will
  be longer than the last export before it, and that a caller who wanted the old content must now
  ask for it by name.

---

### 1.3 Soft delete, undo, trash, and restore

**Why.** `DELETE /api/highlights/{id}` in [xteink_service/api.py](xteink_service/api.py) calls
`SyncState.delete_highlight()`, which runs `DELETE FROM highlights WHERE id = ?`. The row is gone
with no recovery, and the docstring admits the second half of the bug: "Does not un-mark the
Obsidian note". `create_highlight` writes `==text==` into `Books/<slug>.md`, so deleting a
highlight leaves a permanent, unexplained highlight mark in the vault note. Clippings already get
this right, tombstoning in `clippings.delete_clipping()`, but the tombstone also clears `text`, so
even a clipping cannot be restored today.

A trash page fixes recovery but not the common case. Almost every unwanted delete is a misclick
noticed within a second or two, and sending the user to `/trash` to find the row and press Restore
is a lot of ceremony for that. So every destructive route in this item also returns an
`undo_token`, and the page that made the call shows a toast with an Undo button. `/trash` stays as
the durable fallback for anything noticed after the toast is gone.

#### Data model

Highlights gain a nullable soft-delete column plus two indexes:

```sql
-- In SyncState._init_db(), added to the existing ALTER loop:
ALTER TABLE highlights ADD COLUMN deleted_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_highlights_shot_live
    ON highlights (screenshot_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_highlights_deleted
    ON highlights (deleted_at) WHERE deleted_at IS NOT NULL;
```

The `ALTER TABLE` goes into the existing list in `SyncState._init_db()`:

```python
for col, typedef in [
    ("vault_png_path", "TEXT DEFAULT ''"),
    ("ocr_corrected",  "TEXT"),
    ("user_notes",     "TEXT"),
]:
    try:
        conn.execute(f"ALTER TABLE synced_screenshots ADD COLUMN {col} {typedef}")
    except sqlite3.OperationalError:
        pass

for col, typedef in [
    ("deleted_at", "TIMESTAMP"),
]:
    try:
        conn.execute(f"ALTER TABLE highlights ADD COLUMN {col} {typedef}")
    except sqlite3.OperationalError:
        pass
```

Clippings gain three columns, added in `clippings.init_db()` with the same try/except shape:

```sql
ALTER TABLE clippings ADD COLUMN deleted_at     TIMESTAMP;
ALTER TABLE clippings ADD COLUMN deleted_source TEXT NOT NULL DEFAULT '';
ALTER TABLE clippings ADD COLUMN trashed_text   TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_clippings_trash
    ON clippings (deleted_at) WHERE deleted = 1;
```

`ALTER TABLE ... ADD COLUMN ... NOT NULL DEFAULT ''` is legal in SQLite because the default is
non-null and constant.

`trashed_text` is the critical column and exists because of the device contract. A clipping
tombstone sets `text = ''` so the wire shape stays minimal and so a deleted clipping's body is not
paginated back down to the device. That means the text is destroyed at delete time and restore is
impossible. `trashed_text` holds the body while the row sits in the trash. `_row_to_item()` is
unchanged, so `trashed_text` never reaches the device.

`deleted_source` distinguishes a human deleting in the web UI (`'ui'`) from the device reporting an
offline deletion (`'device'`). Both appear in the trash, both can be restored, and restoring a
device deletion legitimately pushes the clipping back to the reader on its next sync.

**The clipping row is never deleted.** Not by the UI delete, not by restore, not by purge. The
device resurrects any id it still holds, so a removed row would come back on the next sync with a
new rowid and the deletion would appear to have silently failed. "Purge" for a clipping therefore
means "clear `trashed_text` and keep the tombstone forever", and the response says so explicitly.

Revisions: every state change in `clippings` takes a fresh `_next_revision(conn)`. Delete takes
one, restore takes another. `revision` is both the version and the pagination cursor, and the
device rejects a cursor that moves backwards, so revisions must only ever increase. Reusing or
decrementing a revision on restore would break the strict pagination contract described in the
module docstring of [xteink_service/clippings.py](xteink_service/clippings.py). Restoring an item
is, from the device's point of view, just another item arriving at a newer revision, which is
exactly how it already handles an edited clipping.

The undo log is a third table, created by `undo.init_db()` and living in `state.db` alongside
everything else in this plan:

```sql
CREATE TABLE IF NOT EXISTS undo_log (
    token      TEXT PRIMARY KEY,               -- uuid4 hex
    kind       TEXT NOT NULL,                  -- clipping_delete | highlight_delete | ...
    label      TEXT NOT NULL DEFAULT '',       -- human text for the toast, e.g. "Deleted 1 clipping"
    payload    TEXT NOT NULL DEFAULT '{}',     -- JSON: everything needed to reverse the action
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    undone_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_undo_created ON undo_log (created_at DESC);
```

The three timestamps are unix integers, matching `clippings.created_at`, not the
`CURRENT_TIMESTAMP` text used by `highlights`. The table is deliberately generic: `kind` names the
action, `payload` carries whatever reversing that action needs, and nothing in `undo.py` knows
what either means.

**Undo here is a reversal that re-asserts prior state from `payload`. It is not a transaction
rollback.** The delete has already committed, the device may already have seen it, and the search
index has already been updated. Undoing runs the same restore path a user would reach from
`/trash`: it sets `deleted = 0`, puts `trashed_text` back into `text`, takes a new revision, and
reindexes. Treating it as a rollback would mean reusing the old revision, which the device would
never see, so the clipping would silently stay missing on the reader. The new revision is the
whole point.

The kinds defined here, with their exact payloads:

```json
{
  "kind": "highlight_delete",
  "payload": {
    "highlight_id": 41,
    "screenshot_id": 112,
    "book_title": "Pastoral",
    "selected_text": "A sentence worth keeping.",
    "unmarked_vault": true,
    "writing_refs": [31]
  }
}
```

```json
{
  "kind": "clipping_delete",
  "payload": {
    "deleted_source": "ui",
    "clippings": [
      { "id": 184, "document": "aaaaaaaa...", "clip_id": "cp-0f3a", "delete_revision": 913 }
    ],
    "writing_refs": [31, 44]
  }
}
```

```json
{
  "kind": "writing_delete",
  "payload": {
    "entry_id": 12,
    "book_slug": "pastoral",
    "title": "On attention",
    "ref_count": 3
  }
}
```

`clipping_delete` takes a list because a device PUT can tombstone several clippings in one request,
and the user should get one toast for that batch rather than seven. `highlight_delete` is a single
object because there is no batch highlight delete. Neither payload stores the passage text: the
clipping body is already preserved in `trashed_text` and the highlight row is still there, so a
second copy would only drift. The payload holds identifiers and the facts needed to phrase the
toast.

`writing_delete` reverses the soft delete of a writing entry from
`PLAN-2-READING-DATA.md` item 2.5. **1.3 fixes its name, its payload, and its toast label; it does
not write its reverser.** The kind is named here because 1.3 owns the `/trash` bucket and the toast
that have to agree with it, and a bucket whose restore has no undo would be the one inconsistent
corner of this item. The function that reverses it is decorated `@undo.register("writing_delete")`
in Plan 2's own module, like every other kind, so `undo.py` still contains no reference to any of
them. `ref_count` is carried for the toast only; the refs themselves are untouched by an entry
delete, because they belong to the entry and go to the trash with it.

#### Deleting something a writing entry cites

This is the case that matters, and 1.3 is where it has to be specified, because 1.3 owns the delete
path for clippings and highlights and 2.5 does not.

**Deleting a cited clipping or highlight must not silently gut the writing that cites it.** An
entry that reads "the line that stayed with me was ((ref:31))" becomes nonsense if the passage
behind ref 31 disappears. The design that prevents it is already in 2.5: `writing_refs.snapshot`
holds a copy of the cited text taken at citation time, so the entry's words survive the source's
deletion, its purge, and a device match-method switch that re-keys it. 1.3 adds the one thing the
snapshot cannot do by itself, which is telling the user the source is gone.

**The delete marks citing refs. It does not cascade and it does not rewrite the entry.** The
entry's `body` is never touched by a delete, in any path, for any reason. Deleting a passage is not
editing someone's prose:

```sql
UPDATE writing_refs
   SET source_state = 'deleted'
 WHERE kind = :kind              -- 'clipping' or 'highlight'
   AND ref_id = :ref_id
   AND source_state = 'live';
```

The reversal, run by restore and by undo alike:

```sql
UPDATE writing_refs
   SET source_state = 'live'
 WHERE id IN (SELECT value FROM json_each(:ref_ids_json))
   AND source_state = 'deleted';
```

The two guards are the contract. `source_state = 'live'` on the way out means a delete never
overwrites a ref that 2.5 had already marked `drifted`, so a restore cannot silently promote a
drifted citation to a clean one. `source_state = 'deleted'` on the way back, plus the explicit id
list from the payload rather than a match on `(kind, ref_id)`, means an undo revives exactly the
refs this delete marked and nothing a different code path marked for a different reason. This is
why both delete payloads carry `writing_refs`.

Every one of these statements is wrapped in `try/except sqlite3.OperationalError`, because
`writing_refs` does not exist until 2.5 lands and 1.3 lands first. On a database without the table
the flip is a no-op, the delete proceeds, and the response reports `writing_refs_marked: 0`. 1.3
must not create either writing table to avoid the try/except; see the same note in 1.1.

Two consequences worth stating plainly:

- **A purge does not touch the snapshot.** Purging a clipping clears `trashed_text`, and the
  clipping's words are then gone from `clippings` forever, but `writing_refs.snapshot` is a
  separate copy and keeps them. The citation still reads correctly, permanently stuck at
  `source_state = 'deleted'` because there is nothing left to restore. That is the intended
  outcome, not a leak.
- **No reindex is needed for the flip.** `source_state` is not part of the indexed body and the
  flip does not touch `snapshot`, so 1.1's `search_docs` row for the entry is unchanged. See the
  freshness table in 1.1.

TTL is 24 hours by default (`_DEFAULT_TTL_S = 24 * 3600`). That is far longer than the few seconds
the toast is on screen, on purpose: dismissing the toast must not destroy the undo. Rows are kept
after they expire or are undone, and `init_db()` sweeps only what is well past useful:

```sql
DELETE FROM undo_log WHERE expires_at < :now - 604800;
```

The seven-day grace is not decoration. Sweeping an undone row immediately would make a repeated
undo return 404 instead of `already_undone`, and sweeping an expired row immediately would turn the
honest 410 ("this expired, use `/trash`") into a confusing 404. Keeping the row until long after
both the toast and the TTL are gone is what stops `/trash` and undo from disagreeing about what
happened to an item.

#### API

| Method | Path | Body | Response |
|---|---|---|---|
| `DELETE` | `/api/highlights/{id}` | none | `{"deleted": 41, "soft": true, "unmarked_vault": true, "writing_refs_marked": 1, "undo_token": "7f3a1c9b4e2d4f6a8b0c1d2e3f405162"}` |
| `POST` | `/api/highlights/{id}/restore` | none | the restored highlight row, same shape as `POST /api/screenshots/{id}/highlights` returns today |
| `DELETE` | `/api/clippings/{id}` | none | `{"deleted": 184, "soft": true, "revision": 913, "writing_refs_marked": 2, "undo_token": "1d4e..."}` |
| `POST` | `/api/clippings/{id}/restore` | none | `{"restored": 184, "revision": 914, "writing_refs_restored": 2}` |
| `GET` | `/api/trash` | none | see below |
| `DELETE` | `/api/trash/highlights/{id}?confirm=1` | none | `{"purged": 41, "row_retained": false, "undo_token": null}` |
| `DELETE` | `/api/trash/clippings/{id}?confirm=1` | none | `{"purged": 184, "row_retained": true, "undo_token": null, "reason": "tombstone kept so the device does not resurrect the id"}` |
| `POST` | `/api/trash/empty?confirm=1` | none | `{"purged": {"highlights": 3, "clippings": 7, "writing": 0}, "rows_retained": 7, "undo_token": null}` |
| `POST` | `/api/undo` | `{"token": "..."}` | see below |
| `GET` | `/api/undo/recent?limit=5` | none | see below |

`writing_refs_marked` and `writing_refs_restored` are how a user finds out that the thing they just
deleted was quoted somewhere. Both are `0` on every database until 2.5 lands, and both count refs,
not entries, because one entry can cite the same clipping twice.

The three purge routes carry the `undo_token` key with a literal `null`. Purge is the one action in
this item that is genuinely not reversible: a purged highlight row is gone and a purged clipping
has had `trashed_text` cleared. Returning the key as `null` keeps one response shape for the client
to read while saying plainly that there is nothing to undo.

`confirm=1` is required on every purge route; without it the endpoint returns 400 with
`{"detail": "Pass confirm=1 to purge permanently"}`. This is a deliberate second gate on top of the
UI confirmation dialog, so a purge can never happen by following a bare link.

`GET /api/trash`:

```json
{
  "counts": { "highlights": 3, "clippings": 7, "writing": 1 },
  "highlights": [
    {
      "id": 41,
      "screenshot_id": 112,
      "selected_text": "A sentence worth keeping.",
      "book_title": "Pastoral",
      "book_slug": "pastoral",
      "sync_date": "2026-07-04",
      "deleted_at": "2026-11-07 09:02:11"
    }
  ],
  "clippings": [
    {
      "id": 184,
      "clip_id": "cp-0f3a",
      "document": "aaaaaaaa...",
      "book_title": "The-Hidden-Keys",
      "book_slug": "the-hidden-keys",
      "chapter": "Chapter One",
      "text": "A sentence worth keeping.",
      "deleted_at": "2026-11-07 09:04:52",
      "deleted_source": "ui",
      "restorable": true
    }
  ],
  "writing": [
    {
      "id": 12,
      "book_title": "Pastoral",
      "book_slug": "pastoral",
      "title": "On attention",
      "entry_date": "2026-11-07",
      "excerpt": "What stayed with me was this: A sentence worth keeping...",
      "ref_count": 3,
      "deleted_at": "2026-11-07 09:06:30",
      "restorable": true
    }
  ]
}
```

`text` in the clipping bucket is read from `trashed_text`. `restorable` is
`trashed_text != ''`; it is false for tombstones created before this change landed, whose body is
unrecoverable. Attempting to restore one returns 409 with
`{"detail": "Clipping text was discarded by an older tombstone and cannot be restored"}`.

The `writing` bucket is the third one and 1.3 adds it, rather than leaving room for it. `excerpt`
is the first 160 characters of the anchor-expanded body, trimmed on a word boundary, so a trashed
entry is recognisable without opening it. `restorable` is always `true` for a writing entry: the
row is intact, nothing about it is cleared on delete, and there is no device to disagree with.
Both the bucket query and the count are wrapped in `try/except sqlite3.OperationalError`, so a
database without `writing_entries` returns `"writing": []` and `"writing": 0` rather than failing
the whole page, which is every database until 2.5 lands.

The two routes that act on that bucket, `POST /api/writing/{id}/restore` and
`DELETE /api/trash/writing/{id}?confirm=1`, are **Plan 2's to implement**, because 2.5 owns the
table. 1.3 fixes their paths and their response shapes here so `/trash` and `POST /api/trash/empty`
can call them without a special case, and so the page does not have to be rewritten when they
arrive. `empty_trash` skips the bucket and reports `"writing": 0` while they do not exist.

Returning named buckets rather than a flat array is what lets the `writing` bucket be added here,
and further buckets be added by Plan 2, without a breaking change.

`POST /api/undo`, body `{"token": "7f3a1c9b4e2d4f6a8b0c1d2e3f405162"}`:

```json
{
  "ok": true,
  "already_undone": false,
  "kind": "clipping_delete",
  "label": "Deleted 1 clipping",
  "result": { "restored": [184], "revision": 914, "writing_refs_restored": 2, "skipped": [] }
}
```

**The endpoint is idempotent.** A second call with the same token returns HTTP 200 and the same
body with `"already_undone": true` and `"result": {}`, and reverses nothing. The mechanism is one
claiming update, taken inside `BEGIN IMMEDIATE` before the reversal runs:

```sql
UPDATE undo_log SET undone_at = :now WHERE token = :token AND undone_at IS NULL;
```

If `cur.rowcount == 0` the row was already claimed, so the handler returns the `already_undone`
body and never calls the reverser. Claiming first, in the same transaction as the reversal, is what
makes a double-click on the Undo button safe. Doing it the other way around would restore the
clipping twice and burn two revisions.

The other outcomes:

| Condition | Status | Body |
|---|---|---|
| No such token | 404 | `{"detail": "Unknown undo token"}` |
| `expires_at <= now` and never undone | 410 | `{"detail": "This undo expired. Restore the item from /trash instead."}` |
| `kind` has no registered reversal | 409 | `{"detail": "No reversal is registered for kind 'x'"}` |
| Target no longer restorable | 200 | `result.skipped` names the ids, `result.restored` omits them |

`GET /api/undo/recent?limit=5` (`Query(default=5, ge=1, le=20)`):

```json
{
  "items": [
    {
      "token": "7f3a1c9b4e2d4f6a8b0c1d2e3f405162",
      "kind": "clipping_delete",
      "label": "Deleted 1 clipping",
      "created_at": 1762502692,
      "expires_at": 1762589092
    }
  ]
}
```

It returns only rows that are still actionable (`undone_at IS NULL AND expires_at > now`), newest
first. `payload` is never returned: it is a server-side detail, and exposing it would invite a
client to reconstruct the reversal itself. This endpoint is what lets the toast survive a page
reload, and it is also how a deletion made on the reader surfaces in the web UI at all, since no
page was open when the sync happened.

`label` is the complete toast line, because `undo_log` has one text column and the client must not
be deriving copy from `kind`. The exact strings:

| Situation | `label` |
|---|---|
| Highlight deleted in the web UI | `Deleted 1 highlight` |
| Clipping deleted in the web UI (`deleted_source = 'ui'`) | `Deleted 1 clipping` |
| Clipping tombstoned by the reader (`deleted_source = 'device'`) | `The reader deleted 1 clipping. Undo sends it back at the next sync.` |
| Either of the above with `writing_refs_marked > 0` | the line above, then ` 2 citations keep its text.` |
| Writing entry deleted in the web UI (`writing_delete`) | `Deleted 1 writing entry` |

The device wording differs because the consequence differs. Undoing a web-UI delete puts a row back
in a list the user is already looking at. Undoing a device tombstone re-asserts the clipping on the
server and pushes it to the reader on its next sync, which is a change the user is making to
another device, and the toast has to say so. Counts are pluralised against the length of
`payload["clippings"]`, so a batch of three reads `The reader deleted 3 clippings.` and so on, and
against the length of `payload["writing_refs"]` for the citation clause, so one reads
`1 citation keeps its text.`

The citation clause is appended rather than replacing the line, and it is appended to both the
web-UI and the device wording, because "this was quoted somewhere" is a fact about the archive and
not about which device did the deleting. It says the writing keeps the text, because that is the
one thing the user would otherwise have to go and check.

#### Service changes

New file [xteink_service/undo.py](xteink_service/undo.py). It is the only module that touches
`undo_log`, and it knows nothing about highlights or clippings:

- `_state_db()` reading `STATE_DB` inside the function, and `_connect()` mirroring
  `clippings._connect()`. No module-level path, no module-level connection.
- `init_db()` issuing the `CREATE TABLE IF NOT EXISTS`, the index, and the sweep. Idempotent, and
  called at the top of each endpoint that touches the table, following the `clippings.py` shape.
- `register(kind: str)`, a decorator that puts a function into a module-level
  `_REVERSERS: dict[str, Callable[[sqlite3.Connection, dict], dict]]`. This is the extension point
  Plan 2 uses.
- `record(kind: str, label: str, payload: dict, ttl_s: int = _DEFAULT_TTL_S) -> str` inserting one
  row with `token = uuid.uuid4().hex` and returning the token.
- `apply(token: str) -> dict` opening `BEGIN IMMEDIATE`, running the claiming update above, looking
  the reverser up by `kind`, calling it with the open connection and the decoded payload, and
  returning `{"ok", "already_undone", "kind", "label", "result"}`. Raises for an unknown token, an
  expired token, and an unregistered kind, which `api.py` maps to 404, 410, and 409.
- `recent(limit: int) -> list[dict]` returning the still-actionable rows described above.

One shared helper owns the citation flip, because four call sites need it and three of them are in
a different module:

- `mark_citations(conn, kind: str, ref_id: int) -> list[int]` running the `'deleted'` update above
  and returning the ids it changed, for the payload.
- `restore_citations(conn, ref_ids: list[int]) -> int` running the `'live'` update above and
  returning the row count.

Both swallow `sqlite3.OperationalError` and return an empty list or zero, which is what makes them
safe to call before 2.5 exists. They go in [xteink_service/undo.py](xteink_service/undo.py)'s
neighbour, not in `undo.py` itself: `undo.py` knows nothing about any domain table and that rule
holds here too. Put them in [xteink_service/state.py](xteink_service/state.py) as module-level
functions taking a connection, next to the other `writing_refs`-free helpers, so both `api.py` and
`clippings.py` can import them without importing each other.

**A reverser receives the connection `apply()` already holds and must not open its own.** Two write
transactions from two connections to the same SQLite file deadlock on `database is locked`, and
`SyncState` and `clippings` both open their own connections today. So each restore path is written
as a connection-taking core plus a thin endpoint wrapper, and the reverser calls the core. The one
side effect that is not SQL, the vault re-mark in `highlight_delete`, runs inside that transaction,
which is acceptable only because `VaultWriter.mark_highlight()` is specified below to swallow
`OSError` and return `False`: it cannot raise, so it cannot abort the undo.

Registration happens at import of the owning module, so `clippings.py` and `api.py` each import
`undo` and decorate their own reverser. `undo.py` imports neither, which keeps the dependency
one-way and avoids a cycle.

[xteink_service/state.py](xteink_service/state.py):

- `SyncState._init_db`: add the `highlights` ALTER loop and the two indexes. Narrow the existing
  bare `except Exception` on the screenshot ALTER loop to `sqlite3.OperationalError` while here.
- `SyncState.delete_highlight(highlight_id) -> bool` becomes
  `UPDATE highlights SET deleted_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL`,
  returning `cur.rowcount > 0`, and calls `search_index.unindex(conn, "highlight", highlight_id)`.
- New `SyncState.restore_highlight(highlight_id) -> dict | None`:
  `UPDATE highlights SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL`, then
  `search_index.index_highlight(conn, highlight_id)`, returning the row.
- New `SyncState.purge_highlight(highlight_id) -> bool`:
  `DELETE FROM highlights WHERE id = ? AND deleted_at IS NOT NULL`. The
  `deleted_at IS NOT NULL` guard is what stops a purge route from hard-deleting a live highlight.
- `SyncState.list_highlights(screenshot_id)`: add `AND deleted_at IS NULL`.
- New `SyncState.list_trashed_highlights() -> list[dict]` joined to `synced_screenshots`.

[xteink_service/vault_writer.py](xteink_service/vault_writer.py) gains two methods, which also
removes the inline vault-editing block currently sitting in `api.create_highlight`:

- `mark_highlight(self, book_title: str, selected_text: str) -> bool`. Moves the existing logic
  verbatim: open `Books/<_sanitize(book_title)>.md`, and if `f"=={selected_text}=="` is not already
  present and `selected_text` is, replace the first occurrence. Returns whether the file changed.
- `unmark_highlight(self, book_title: str, selected_text: str) -> bool`. The exact inverse:
  replace the first occurrence of `f"=={selected_text}=="` with `selected_text`. Returns whether
  the file changed. Returns `False` when the note does not exist, when the mark is absent, or on
  any `OSError`; a vault write failure must stay non-fatal, matching how `create_highlight` already
  treats it.

Both are symmetric by construction, which is the only reason the unmark is reliable: a highlight
whose text spans lines was never marked in the first place (the marking replace is a literal
substring match against the note, and the OCR callout prefixes each line with `> `), so the unmark
correctly finds nothing to do.

[xteink_service/api.py](xteink_service/api.py):

- `create_highlight`: replace the inline vault block with
  `VaultWriter(str(_vault_path())).mark_highlight(shot["book_title"], body.selected_text)`.
- `delete_highlight`: soft delete, then `unmark_highlight`, then `mark_citations(conn,
  "highlight", id)`, then
  `undo.record("highlight_delete", "Deleted 1 highlight", payload)`, and report all four in the
  response. The row is read before the soft delete so the payload can carry `book_title` and
  `selected_text`.
- New `_restore_highlight(conn, highlight_id) -> dict | None`, with `restore_highlight` as the thin
  endpoint over it, so the reverser can reuse the core on an open connection. It calls
  `restore_citations` with the ids from the payload on the undo path, and with the ids currently
  `deleted` for that `(kind, ref_id)` on the `/trash` restore path, where no payload exists.
- New `_undo_highlight_delete(conn, payload)` decorated with `@undo.register("highlight_delete")`,
  calling `_restore_highlight` and then `mark_highlight`. Putting the mark back is the half of the
  undo the user actually sees in Obsidian, so it is part of the reversal, not a follow-up.
- `list_all_highlights` and `list_book_highlights`: add `AND h.deleted_at IS NULL`.
- New `trash`, `purge_trashed_highlight`, `purge_trashed_clipping`, `empty_trash`.
- New `run_undo` (`POST /api/undo`) and `list_recent_undo` (`GET /api/undo/recent`), both thin:
  they call `undo.apply()` / `undo.recent()` and translate the three failure cases into 404, 410,
  and 409.

[xteink_service/clippings.py](xteink_service/clippings.py):

- `init_db()`: add the three `ALTER TABLE` statements and the index.
- `_apply()` tombstone branch: set `deleted_source = 'device'`, `deleted_at = CURRENT_TIMESTAMP`,
  and `trashed_text = COALESCE(NULLIF(clippings.text, ''), clippings.trashed_text)` so the body is
  preserved before `text` is cleared. The `ON CONFLICT` clause already runs as an update, so the
  old row's `text` is readable as the pre-update value within the same statement by referencing the
  unqualified column name.
- `_apply()` live branch: clear `deleted_at`, `deleted_source`, and `trashed_text` on a re-upload,
  because a device re-uploading a clipping is an implicit restore.
- `delete_clipping()`: set `trashed_text = text` before clearing `text`, set
  `deleted_source = 'ui'` and `deleted_at = CURRENT_TIMESTAMP`, take a fresh revision, unindex,
  call `mark_citations(conn, "clipping", id)`, and
  record a `clipping_delete` undo entry carrying that one clipping and the marked ref ids.
- New `_restore_clipping(conn, clipping_id) -> dict | None`: guard on `trashed_text != ''`, then
  `UPDATE clippings SET deleted = 0, text = trashed_text, trashed_text = '', deleted_at = NULL, deleted_source = '', revision = ? WHERE id = ?`
  with a fresh `_next_revision(conn)`, then reindex. The fresh revision is mandatory, on the restore
  path and the undo path alike. A restore that reuses the old revision sits below every cursor the
  device has already passed, so the device never downloads the row again and the clipping stays
  missing on the reader even though the server believes it is back.
- `restore_clipping(clipping_id)`: the endpoint, a thin wrapper that opens the connection and calls
  `_restore_clipping`, then `restore_citations` for the refs currently `deleted` against that id.
- New `_undo_clipping_delete(conn, payload)` decorated with `@undo.register("clipping_delete")`,
  calling `_restore_clipping` for each entry in `payload["clippings"]`, collecting the new
  revisions, calling `restore_citations(conn, payload["writing_refs"])` once for the batch, and
  putting any id whose `trashed_text` is now empty into `result["skipped"]` rather
  than raising.
- `upload_clippings()`: after the loop, if the batch produced any tombstones, record one
  `clipping_delete` entry with `deleted_source = 'device'` covering all of them, and call
  `mark_citations` once per tombstoned id, collecting every marked ref id into that single entry's
  payload. One entry per PUT, not one per item, so a sync that removes seven clippings produces one
  toast.
- New `list_trashed_clippings()` returning the shape above.
- New `purge_clipping(clipping_id)`:
  `UPDATE clippings SET trashed_text = '' WHERE id = ? AND deleted = 1`. No `DELETE`.

#### UI

- [web/src/lib/api.ts](web/src/lib/api.ts): add a `trash` namespace with `get()`,
  `restoreHighlight(id)`, `restoreClipping(id)`, `purgeHighlight(id)`, `purgeClipping(id)`,
  `empty()`. All purge wrappers append `?confirm=1`. Add `TrashResponse`, `TrashedHighlight`,
  `TrashedClipping`, and `TrashedWritingEntry` interfaces; `TrashResponse.writing` is typed and
  always present, while `restoreWriting(id)` and `purgeWriting(id)` are Plan 2's to add alongside
  the routes they call. Add an `undo` namespace with `run(token)` posting `{ token }` to
  `/api/undo` and `recent(limit = 5)`, plus `UndoEntry` and `UndoRunResponse` interfaces. Every
  delete wrapper's return type gains `undo_token: string | null`.
- New [web/src/lib/undo.svelte.ts](web/src/lib/undo.svelte.ts): the toast's state, written as a
  single shared instance in exactly the style of
  [web/src/lib/panel.svelte.ts](web/src/lib/panel.svelte.ts). Module-level `$state` plus one
  exported object of getters and methods. Not a factory and not a context, because there is one
  toast for the whole app and a second instance would mean two toasts fighting over the same
  corner.

```ts
export interface UndoEntry {
  token: string;
  kind: string;
  label: string;
  created_at: number;
  expires_at: number;
}

export const undo = {
  get entry(): UndoEntry | null { ... },
  get busy(): boolean { ... },
  offer(entry: UndoEntry): void { ... },    // show it, arm the dismiss timer
  dismiss(): void { ... },                  // clears the timer, leaves the server token alone
  async run(): Promise<void> { ... },       // POST /api/undo, then dismiss
  async rehydrate(): Promise<void> { ... }, // GET /api/undo/recent?limit=1 on mount
};
```

  The toast auto-dismisses after 10 seconds. `dismiss()` only hides it; the token lives for 24
  hours and the item is still in `/trash`, so a user who dismisses the toast has lost nothing.
  `run()` sets `busy` for the duration so a double-click cannot fire two requests, which pairs with
  the server-side idempotency rather than replacing it.
- New [web/src/lib/components/UndoToast.svelte](web/src/lib/components/UndoToast.svelte): renders
  nothing when `undo.entry` is null, otherwise a fixed-position bar showing `undo.entry.label`, an
  "Undo" button bound to `undo.run`, and a dismiss button. `role="status"` and `aria-live="polite"`
  so the label is announced without stealing focus. The Undo button is disabled while `undo.busy`.
  No new dependency and no colour literals; it uses the existing tokens.
- [web/src/routes/+layout.svelte](web/src/routes/+layout.svelte): import `UndoToast` and mount it
  once, next to the existing `<ScreenshotPanel />`, and call `undo.rehydrate()` from the existing
  `onMount`. Mounting it in the layout rather than per page is what lets a toast raised on
  `/clippings` survive navigating to `/books`.
- New [web/src/routes/trash/+page.ts](web/src/routes/trash/+page.ts) and
  [web/src/routes/trash/+page.svelte](web/src/routes/trash/+page.svelte). Three sections, each
  listing rows with a "Restore" button and a "Purge" button. Purge opens a confirmation that names
  the item and, for clippings, states that the tombstone row is kept so the reader does not
  resurrect the id. Clippings with `restorable: false` show the Restore button disabled with a
  title explaining why. The sections are rendered by iterating the `counts` keys, not by writing
  three blocks, and a bucket with an empty array renders nothing, so the `writing` section is
  simply absent until 2.5 lands and needs no second pass over this page when it does.
- [web/src/routes/+layout.svelte](web/src/routes/+layout.svelte): add `{ href: '/trash', label: 'Trash' }`
  to `navLinks`. The existing `isActive()` helper handles it with no change.
- [web/src/routes/books/[book]/+page.svelte](web/src/routes/books/%5Bbook%5D/+page.svelte),
  [web/src/routes/highlights/+page.svelte](web/src/routes/highlights/+page.svelte),
  [web/src/routes/clippings/+page.svelte](web/src/routes/clippings/+page.svelte): their optimistic
  `filter(...)` after a delete is still correct, because the item leaves the live list either way.
  The one change is to hand the delete response to `undo.offer(...)` when `undo_token` is not null.
  After a successful undo, re-run the page's existing `load()` rather than splicing the row back
  into the array: the list order is server-derived, and guessing the index is how an off-by-one bug
  gets in. Update the delete button's title to "Move to trash".

#### Backfill / migration

- The three `ALTER TABLE` statements are the whole migration, and all are additive with safe
  defaults. An existing `state.db` picks them up the next time
  `SyncState._init_db()` or `clippings.init_db()` runs, which is on the first write or the first
  clipping endpoint call. Re-running is a no-op because the `ALTER` raises
  `sqlite3.OperationalError` and is swallowed.
- **No data backfill is possible or needed for already-hard-deleted highlights.** They are gone.
  Existing live highlights have `deleted_at IS NULL` by virtue of the column default, which is
  exactly right.
- Existing clipping tombstones (rows with `deleted = 1` from before this change) get
  `deleted_source = ''`, `deleted_at = NULL`, and `trashed_text = ''`. They appear in the trash
  with `restorable: false` and a null `deleted_at`. Sort them last. Do not attempt to recover their
  text from the vault; the mapping from a clipping to a vault line is not reliable enough.
- `undo_log` is created empty by `undo.init_db()` on the first call and there is nothing to
  backfill. Deletions performed before this change landed have no token, which is correct: they
  are in `/trash` and that is the only recovery path they ever had.
- `writing_entries` and `writing_refs` are not created here and not backfilled here. Until 2.5
  lands, every citation query is a swallowed `sqlite3.OperationalError`, the `writing` trash bucket
  is empty, and both citation counters are zero. Nothing about a delete or an undo behaves
  differently; it simply has nothing to mark. Passages deleted before 2.5 lands were never cited by
  anything, so there is no historical state to reconstruct.
- Orphaned `==` marks already written into vault notes by past deletions are **not** cleaned up
  automatically. `POST /api/vault/rebuild` does not remove them either, because it appends rather
  than rewriting. Cleaning them is a manual Obsidian search for `==` and is listed in "Out of
  scope".

#### Risks

- **Restore pushes a clipping back to the device.** This is intended, but it means a restore
  performed while the reader is offline takes effect only at the next sync, and the user sees no
  feedback until then. The response includes the new `revision` so the UI can say "will reappear on
  the reader at the next sync".
- **Undo is a reversal, not a rollback.** Nothing is wound back. The undo re-asserts the prior
  state from `payload` on top of whatever the database looks like now, which is why it has to take
  a new revision and re-run the vault mark rather than restoring an old snapshot. If a future kind
  is added that cannot be expressed that way, it does not belong in `undo_log`.
- **The toast window and the token lifetime are different on purpose.** The toast disappears after
  ten seconds, the token lives 24 hours, and `/trash` holds the item indefinitely. Collapsing these
  into one number is tempting and wrong: making the token expire with the toast would mean looking
  away from the screen destroys the cheap recovery path, and keeping the toast up for 24 hours
  would be unusable.
- **An undo entry can outlive its target.** A user can delete a clipping, then empty the trash,
  while the token is still live. The reverser must re-check `trashed_text != ''` for each id and
  report the misses in `result["skipped"]` instead of restoring an empty body. The same applies to
  a purged highlight, whose row is genuinely gone. Undo is best-effort against current state, and
  the response has to say which ids it could not bring back.
- **Registry lookup depends on import order.** `apply()` can only reverse a kind whose owning
  module has been imported and has run its `@undo.register` decorator. In the running service every
  module is imported by the app, but a test that imports only `undo` will get the 409. That is the
  correct failure: better a clear "no reversal is registered" than a silent no-op that reports
  success.
- **Undoing a device tombstone talks back to the reader.** It is the same mechanism as a manual
  restore, so it is safe, but the user is changing another device from a toast. The distinct label
  is the only thing stopping that from being a surprise, so the two strings are part of the
  contract, not copy that can be tidied later.
- **The `==` unmark is a literal substring replace.** If the user edited the note in Obsidian
  between the mark and the unmark, the mark is not found and the note keeps the `==`. The method
  returns `False` in that case and the API reports `unmarked_vault: false` rather than claiming
  success.
- **Purge semantics differ between the two kinds.** A purged highlight is really gone; a purged
  clipping keeps its tombstone row. The confirmation dialog must say so, or the user will assume
  the row count in `state.db` is wrong.
- **Tombstone growth.** Clipping tombstones accumulate forever by design. At the scale of a
  personal archive this is a few kilobytes a year. It is not a leak to fix; it is the protocol.
- **A citation flip is a write into another plan's table.** 1.3 updates `writing_refs` while 2.5
  owns it. The coupling is deliberate and narrow: two columns of one table, through two helpers,
  with every statement guarded. The alternative, a delete hook that 2.5 registers, is a second
  registry for one caller. If 2.5 ends up needing to react to a delete in some richer way, that is
  the moment to build the hook, not now.
- **The snapshot is the only surviving copy after a purge.** Once a clipping is purged, its words
  exist in `writing_refs.snapshot` and nowhere else. That is the intended behaviour, but it means
  a future bulk operation on `writing_refs` can destroy text that cannot be recovered from any
  other table. Treat `snapshot` as archive data, not as a cache.
- **A delete says the citation survived; it does not show it.** The toast reports that citations
  keep the text, but the user cannot see the affected entries from the toast and `/trash` does not
  list them either. Linking a deleted passage to the entries quoting it is a reasonable thing to
  want and it is 2.5's to build, because it needs the entry list UI that does not exist yet.

---

### 1.4 "On this day" and a review queue

**Why.** Every passage in the archive already carries a date:
`synced_screenshots.sync_date` (a `YYYY-MM-DD` text column),
`highlights.created_at` (a SQLite `CURRENT_TIMESTAMP`, stored as `YYYY-MM-DD HH:MM:SS` in UTC), and
`clippings.created_at` (a unix integer from the device). Nothing in the UI uses those dates for
resurfacing, so a passage captured two years ago is only findable by searching for words the user
has to already remember. This is the most commonplace-book-shaped feature in the whole of
[IDEAS.md](IDEAS.md) and it needs no new data to work.

#### Data model

`GET /api/on-this-day` needs no schema change. The spaced-repetition strategy needs one table:

```sql
CREATE TABLE IF NOT EXISTS review_log (
    kind          TEXT    NOT NULL,              -- screenshot | highlight | clipping
    ref_id        INTEGER NOT NULL,
    last_shown    TIMESTAMP,
    shown_count   INTEGER NOT NULL DEFAULT 0,
    interval_days INTEGER NOT NULL DEFAULT 1,
    next_due      TEXT    NOT NULL DEFAULT '',   -- YYYY-MM-DD
    last_rating   TEXT    NOT NULL DEFAULT '',   -- '' | again | good
    PRIMARY KEY (kind, ref_id)
);

CREATE INDEX IF NOT EXISTS idx_review_due ON review_log (next_due);
```

An item with no `review_log` row has never been surfaced and is always due. That is what makes the
table sparse: it only ever holds items the user has actually reviewed, so it stays small regardless
of archive size.

#### API

`GET /api/on-this-day`

| Param | Type | Default | Notes |
|---|---|---|---|
| `date` | str | today (local) | `YYYY-MM-DD`. Returns 400 on an unparseable value. |
| `limit` | int | `50` | `Query(default=50, ge=1, le=200)`. Applied per year, then overall. |

```json
{
  "date": "2026-11-07",
  "total": 7,
  "years": [
    {
      "year": 2025,
      "years_ago": 1,
      "items": [
        {
          "kind": "screenshot",
          "ref_id": 112,
          "screenshot_id": 112,
          "book_title": "Pastoral",
          "book_slug": "pastoral",
          "date": "2025-11-07",
          "text": "The first 240 characters of the OCR text, trimmed on a word boundary..."
        }
      ]
    }
  ]
}
```

`years` is ordered most recent first and never contains the current year. An empty archive or a
date with no history returns `{"date": ..., "total": 0, "years": []}`, not a 404.

The three source queries, matching `MM-DD` and excluding the current year:

```sql
-- screenshots
SELECT id, book_title, sync_date,
       COALESCE(NULLIF(ocr_corrected, ''), ocr_text, '') AS body
FROM synced_screenshots
WHERE substr(sync_date, 6, 5) = :mmdd
  AND substr(sync_date, 1, 4) <  :year
ORDER BY sync_date DESC, id DESC
LIMIT :limit;

-- highlights
SELECT h.id, h.selected_text, h.screenshot_id, s.book_title,
       substr(h.created_at, 1, 10) AS day
FROM highlights h
JOIN synced_screenshots s ON s.id = h.screenshot_id
WHERE substr(h.created_at, 6, 5) = :mmdd
  AND substr(h.created_at, 1, 4) <  :year
  AND h.deleted_at IS NULL            -- after 1.3; omit if 1.3 has not landed
ORDER BY h.created_at DESC
LIMIT :limit;

-- clippings
SELECT c.id, c.text, c.chapter, a.title AS book_title,
       date(c.created_at, 'unixepoch') AS day
FROM clippings c
LEFT JOIN document_aliases a ON a.hash = c.document
WHERE c.deleted = 0
  AND c.created_at > 0
  AND strftime('%m-%d', c.created_at, 'unixepoch') = :mmdd
  AND strftime('%Y',    c.created_at, 'unixepoch') <  :year
ORDER BY c.created_at DESC
LIMIT :limit;
```

Clippings with `created_at = 0` are excluded, because the device writes 0 when its clock was never
set, which would otherwise put a pile of passages on 1 January 1970. This is the same guard that
[web/src/lib/components/ClippingCard.svelte](web/src/lib/components/ClippingCard.svelte) already
applies when rendering `savedOn`.

`GET /api/review`

| Param | Type | Default | Notes |
|---|---|---|---|
| `strategy` | str | `spaced` | `spaced` or `random`. Anything else returns 400. |
| `limit` | int | `10` | `Query(default=10, ge=1, le=50)` |
| `kinds` | str | `highlight,clipping` | Comma-separated. Screenshots are excluded by default because an OCR page is a worse review unit than a chosen passage. |
| `seed` | str | today's date | `random` only. Makes the home-page card stable for a day instead of reshuffling on every navigation. |

```json
{
  "strategy": "spaced",
  "limit": 10,
  "total_candidates": 318,
  "items": [
    {
      "kind": "highlight",
      "ref_id": 41,
      "screenshot_id": 112,
      "book_title": "Pastoral",
      "book_slug": "pastoral",
      "date": "2026-07-04",
      "text": "A sentence worth keeping.",
      "shown_count": 2,
      "last_shown": "2026-10-24 18:02:11",
      "next_due": "2026-11-01"
    }
  ]
}
```

`POST /api/review/{kind}/{ref_id}/seen`

Body: `{"rating": "good"}` or `{"rating": "again"}`. Response:
`{"kind": "highlight", "ref_id": 41, "shown_count": 3, "interval_days": 4, "next_due": "2026-11-11"}`.

Scheduling rule, deliberately simpler than SM-2: `good` doubles `interval_days` (capped at 365),
`again` resets it to 1. A first review starts at 1. `next_due = date(now, '+N days')`.
`shown_count` increments on every call. No ease factor, no lapse counter, no fuzz.

#### Service changes

New file [xteink_service/resurface.py](xteink_service/resurface.py), holding both features because
they share the same candidate queries and the same row-to-item projection:

- `_state_db()` reading the env var inside the function.
- `init_db(conn)` creating `review_log` and its index. Called at the top of each endpoint, matching
  the `clippings.py` pattern.
- `on_this_day(db_path, day: date, limit: int) -> dict`. Runs the three queries, projects each row
  to the common item shape, buckets by year, truncates `text` to 240 characters on a word boundary,
  and wraps each query in `try/except sqlite3.OperationalError` so a database missing `highlights`
  or `clippings` degrades to the kinds that exist.
- `review(db_path, strategy, kinds, limit, seed) -> dict`.
  - `random`: select only `(kind, ref_id)` pairs for the candidate set, shuffle with
    `random.Random(seed)`, take `limit`, then fetch the full rows for that small set. An id list of
    fifty thousand pairs is a few megabytes at most and is bounded by the archive size, not by the
    page. State the ceiling in a `ponytail:` comment, with "move to a `review_log`-backed cursor"
    as the upgrade path.
  - `spaced`: candidates are items with no `review_log` row (ordered oldest capture first) followed
    by items whose `next_due <= today` (ordered by `next_due`, then `last_shown`). Implement as a
    `LEFT JOIN review_log r ON r.kind = ... AND r.ref_id = ...` with
    `WHERE r.ref_id IS NULL OR r.next_due <= :today` and
    `ORDER BY (r.ref_id IS NOT NULL), r.next_due, ...`.
- `mark_seen(db_path, kind, ref_id, rating) -> dict` running the upsert:

```sql
INSERT INTO review_log (kind, ref_id, last_shown, shown_count, interval_days, next_due, last_rating)
VALUES (?, ?, CURRENT_TIMESTAMP, 1, ?, ?, ?)
ON CONFLICT(kind, ref_id) DO UPDATE SET
    last_shown    = CURRENT_TIMESTAMP,
    shown_count   = shown_count + 1,
    interval_days = excluded.interval_days,
    next_due      = excluded.next_due,
    last_rating   = excluded.last_rating;
```

  with `interval_days` and `next_due` computed in Python before the call, because the previous
  interval has to be read first to double it.

[xteink_service/api.py](xteink_service/api.py) gains three thin endpoints delegating to the module:
`on_this_day`, `review`, `review_seen`. None of them is added to `_PUBLIC_ALLOWED`.

#### UI

**The home page is a redirect stub.** [web/src/routes/+page.svelte](web/src/routes/+page.svelte)
contains only `onMount(() => goto('/books', { replaceState: true }))`, with a comment explaining
that `replaceState` is required so hardware Back on Android does not loop. Do not turn `/` into a
dashboard in Plan 1: that would reintroduce the Back loop and is a larger change than this item
justifies. The de-facto home page is `/books`.

- [web/src/routes/books/+page.ts](web/src/routes/books/+page.ts): also load
  `api.onThisDay().catch(() => null)`.
- [web/src/routes/books/+page.svelte](web/src/routes/books/+page.svelte): render a new
  `OnThisDay.svelte` above the book grid, and render nothing at all when `total` is 0.
- New [web/src/lib/components/OnThisDay.svelte](web/src/lib/components/OnThisDay.svelte):
  props `{ data: OnThisDayResponse }`. One section per year with a "N years ago" caption, each item
  a quote card that opens the screenshot panel via `panel.open(screenshot_id)` when
  `screenshot_id` is non-null, and links to `/books/<book_title>` otherwise.
- New [web/src/routes/review/+page.ts](web/src/routes/review/+page.ts) and
  [web/src/routes/review/+page.svelte](web/src/routes/review/+page.svelte): one passage at a time,
  with "Again" and "Good" buttons that call `POST /api/review/{kind}/{ref_id}/seen` and advance,
  plus a strategy toggle that writes `?strategy=` into the URL. When the queue empties, show the
  count reviewed and a link back to `/books`.
- [web/src/routes/+layout.svelte](web/src/routes/+layout.svelte): add
  `{ href: '/review', label: 'Review' }` to `navLinks`.
- [web/src/lib/api.ts](web/src/lib/api.ts): add `onThisDay(date?)`, `review(opts)`, and
  `reviewSeen(kind, refId, rating)` plus the `OnThisDayResponse`, `ReviewResponse`, and
  `ReviewItem` interfaces.

#### Backfill / migration

- `review_log` is created empty by `init_db()`. There is nothing to backfill: an absent row means
  "never reviewed", which is the correct starting state for every existing passage.
- "On this day" needs no migration at all; it reads existing date columns.
- Both are idempotent and re-runnable by construction. `mark_seen` is the only write, and running
  it twice on the same item simply advances the interval twice, which is correct behaviour rather
  than corruption.

#### Risks

- **Date semantics are inconsistent across the three sources.** `sync_date` is a local date written
  by the archiver, `highlights.created_at` is UTC from SQLite, and `clippings.created_at` is
  whatever the device's clock said. Near midnight, a passage can land on a different "day" than the
  user remembers. This is inherent to the existing data and is not worth normalising now; note it
  in the UI copy if it becomes confusing.
- **FAT timestamps.** [CLAUDE.md](CLAUDE.md) records that device filesystem timestamps are not
  reliable and that the archiver falls back to the current time. Some historical `sync_date` values
  are therefore the archive date, not the reading date. "On this day" surfaces that inaccuracy
  rather than causing it.
- **Random strategy memory.** Loading every candidate id is bounded by archive size. At the scale
  this tool targets it is fine, but it is the one place in Plan 1 with an unbounded-ish read, so it
  gets the `ponytail:` ceiling comment.
- **Empty states dominate early.** A young archive has no prior years, so "On this day" renders
  nothing most days. That is correct, and the component must render nothing rather than an empty
  heading.

---

### 1.5 Procedural placeholder covers

**Why.** There is no cover art anywhere in the UI.
[web/src/lib/components/BookCard.svelte](web/src/lib/components/BookCard.svelte) renders a title and
a one-line meta string inside a bordered box, and the sidebar book index in
[web/src/routes/+layout.svelte](web/src/routes/+layout.svelte) is a list of text links, so every
book looks identical and the grid at `/books` is hard to scan. A deterministic generated cover
gives each book a stable visual identity with no metadata provider, no network request, and no
backend work.

#### Data model

**None.** No table, no column, no endpoint. The slug is the only input.

#### API

**None.** This item adds no backend surface.

#### Service changes

**None.**

#### UI

New [web/src/lib/placeholder.ts](web/src/lib/placeholder.ts), a pure module with no Svelte import
so it can be unit tested directly:

```ts
export interface CoverPalette { cloth: string; ink: string; rule: string }

/** FNV-1a 32-bit. Stable across browsers and across sessions. */
export function hashSlug(slug: string): number;

/** Deterministic palette + geometry for a slug. */
export function coverStyle(slug: string): {
  palette: CoverPalette;
  ruleCount: number;   // 2..4 horizontal rules
  ruleOffset: number;  // 0..1, vertical position of the rule block
  grain: number;       // 0..1, opacity of the cloth texture overlay
};
```

`hashSlug` is FNV-1a because it is six lines, has no dependency, and has good avalanche for short
ASCII strings. `coverStyle` derives each field from a different bit range of the hash so two slugs
differing by one character get a visibly different cover.

**One style only: clothbound.** A flat cloth-coloured field, a darker inset border, two to four
horizontal rules in the upper third, the title set in the serif stack over them, and a subtle SVG
`feTurbulence` grain. No second style, no style prop, no registry. Porting all nine BookOrbit
styles would be nine times the surface for the same result.

New [web/src/lib/components/PlaceholderCover.svelte](web/src/lib/components/PlaceholderCover.svelte):

```ts
let {
  slug,
  title = '',
  width = 120,
  height = 180,
}: { slug: string; title?: string; width?: number; height?: number } = $props();
```

It renders a single inline `<svg viewBox="0 0 120 180" role="img" aria-label={title || slug}>`.
No `<img>`, no data URI, no canvas. The `aria-label` is what keeps the grid accessible, since the
cover carries no real text for a screen reader.

The palette is eight entries, indexed by `hash % 8`, expressed with the tokens from 1.6:

```ts
const PALETTES: CoverPalette[] = [
  { cloth: 'oklch(42% 0.07 25)',  ink: 'oklch(92% 0.02 25)',  rule: 'oklch(58% 0.08 25)'  },
  // ... seven more, varying hue only, holding lightness and chroma constant
];
```

Holding lightness and chroma constant across the eight entries and varying only hue is what makes
the grid look like a set of books rather than a set of unrelated swatches, and it is the same
principle 1.6 applies to the interface itself.

Mount points:

- [web/src/lib/components/BookCard.svelte](web/src/lib/components/BookCard.svelte): above
  `.book-card-body`, at the card's full width. Compute the slug client-side. The server already
  returns `book_title`; deriving the slug in TypeScript would require reimplementing
  `canonical_book_slug()`, so instead pass `book.book_title` as the hash input. The hash does not
  need to agree with the backend slug, only to be stable for a given title, and the title is stable.
  Document this in a one-line comment so nobody "fixes" it later by adding a slug field to the API.
- [web/src/routes/books/[book]/+page.svelte](web/src/routes/books/%5Bbook%5D/+page.svelte): a
  60 by 90 cover to the left of the `h1` in the title row.
- [web/src/styles/app.css](web/src/styles/app.css): `.book-card img, .book-card svg { width: 100%; display: block; }` and
  a `.gallery` column-width bump from 200px to 220px so the cover plus two meta lines fit without
  wrapping.

Do **not** add covers to the sidebar book index or to search results in Plan 1. Both are dense text
lists where a cover makes scanning worse, and the sidebar already hides itself in the landscape
breakpoint.

#### Backfill / migration

None. The cover is derived on every render from a string already present in the component's props.
There is no cache to invalidate, which is the main reason to prefer a procedural cover over a
stored one at this stage.

#### Risks

- **`oklch()` in the Capacitor WebView.** The web UI is also packaged as an Android app via
  `web/capacitor.config.ts` and `web/android/`. `oklch()` requires Chrome 111 or newer; an older
  Android System WebView renders the fill as invalid and the cover comes out transparent. Verify on
  the real device before merging. The mitigation is a one-line change: swap `PALETTES` for hex
  triples, since every consumer reads the palette through `coverStyle()`.
- **Hash input is the title, not the canonical slug.** Renaming a book's screenshot folder changes
  its cover. Acceptable, and the alternative is a backend round trip for a decorative element.
- **Visual collision.** Eight palettes means a library of thirty books has repeats. That is fine;
  the rule count, rule offset, and grain also vary, so two books sharing a palette still differ.

---

### 1.6 OKLCH theme tokens

**Why.** [web/src/styles/app.css](web/src/styles/app.css) opens with nine hardcoded Catppuccin
Macchiato hex values (`--bg: #24273a`, `--bg-card: #363a4f`, and so on) with no relationship
between them, so changing the surface tint means hand-picking nine new hex codes and there is no
light mode at all. Worse, colour has leaked out of the stylesheet into components: `#eed49f` and
`#24273a` are repeated verbatim in three places
([web/src/styles/app.css](web/src/styles/app.css) `.sidebar-mark`,
[web/src/routes/highlights/+page.svelte](web/src/routes/highlights/+page.svelte),
[web/src/routes/search/+page.svelte](web/src/routes/search/+page.svelte)),
[web/src/lib/components/ReadingCalendar.svelte](web/src/lib/components/ReadingCalendar.svelte) has
four `rgba(166, 218, 149, ...)` heat levels, and
[web/src/routes/aliases/+page.svelte](web/src/routes/aliases/+page.svelte),
[web/src/routes/log/+page.svelte](web/src/routes/log/+page.svelte), and
[web/src/lib/components/TbrRow.svelte](web/src/lib/components/TbrRow.svelte) each carry a one-off
status colour.

#### Data model

**None.** CSS only.

#### API

**None.**

#### Service changes

**None.**

#### UI

Rewrite the `:root` block of [web/src/styles/app.css](web/src/styles/app.css). Every neutral
surface derives from one hue and one chroma; dark and light differ only in the lightness ramp.

```css
:root {
  /* ---- the two numbers that drive every neutral ---- */
  --tint-h: 265;        /* hue of all greys */
  --tint-c: 0.022;      /* chroma of all greys; 0 gives true neutral */

  /* ---- accent ---- */
  --accent-h: 250;
  --accent-c: 0.11;

  /* ---- lightness ramp (dark is the default, matching today) ---- */
  --l-bg: 23%;
  --l-sunken: 19%;
  --l-surface: 29%;
  --l-border: 38%;
  --l-muted: 76%;
  --l-text: 88%;
  --l-accent: 72%;
  --l-accent-hi: 80%;

  /* ---- derived neutrals: these names are unchanged so no component edits ---- */
  --bg:          oklch(var(--l-bg)      var(--tint-c) var(--tint-h));
  --bg-sidebar:  oklch(var(--l-sunken)  var(--tint-c) var(--tint-h));
  --bg-card:     oklch(var(--l-surface) var(--tint-c) var(--tint-h));
  --border:      oklch(var(--l-border)  var(--tint-c) var(--tint-h));
  --active-bg:   oklch(var(--l-border)  var(--tint-c) var(--tint-h));
  --text:        oklch(var(--l-text)    var(--tint-c) var(--tint-h));
  --text-muted:  oklch(var(--l-muted)   var(--tint-c) var(--tint-h));
  --link:        oklch(var(--l-accent)    var(--accent-c) var(--accent-h));
  --link-hover:  oklch(var(--l-accent-hi) var(--accent-c) var(--accent-h));

  /* ---- semantic ramps, replacing the one-off colours in components ---- */
  --mark-h: 80;
  --mark-bg: oklch(84% 0.12 var(--mark-h));
  --mark-fg: oklch(var(--l-bg) var(--tint-c) var(--tint-h));
  --mark-stroke: oklch(84% 0.12 var(--mark-h) / 0.9);
  --mark-fill:   oklch(84% 0.12 var(--mark-h) / 0.3);

  --heat-h: 145;
  --heat-0: oklch(var(--l-surface) var(--tint-c) var(--tint-h));
  --heat-1: oklch(45% 0.06 var(--heat-h));
  --heat-2: oklch(57% 0.10 var(--heat-h));
  --heat-3: oklch(68% 0.14 var(--heat-h));
  --heat-4: oklch(79% 0.17 var(--heat-h));

  --ok:     oklch(78% 0.13 165);
  --warn:   oklch(78% 0.13  60);
  --danger: oklch(70% 0.17  25);

  /* ---- unchanged ---- */
  --sidebar-width: 14rem; --panel-width: 32rem; --gap: 1rem; --radius: 4px;
  --font-sans: system-ui, -apple-system, sans-serif;
  --font-serif: Georgia, serif;
  --font-mono: ui-monospace, monospace;
  color-scheme: dark;
}

/* Light mode shifts lightness only. Hue and chroma are untouched, which is
   what keeps the two modes recognisably the same interface. */
:root[data-theme='light'] {
  --l-bg: 97%;
  --l-sunken: 94%;
  --l-surface: 99%;
  --l-border: 86%;
  --l-muted: 48%;
  --l-text: 26%;
  --l-accent: 48%;
  --l-accent-hi: 40%;
  --mark-fg: oklch(26% var(--tint-c) var(--tint-h));
  --heat-0: oklch(var(--l-sunken) var(--tint-c) var(--tint-h));
  color-scheme: light;
}
```

**Keeping the nine existing variable names is the whole trick.** `--bg`, `--bg-card`,
`--bg-sidebar`, `--border`, `--text`, `--text-muted`, `--link`, `--link-hover`, and `--active-bg`
are referenced throughout the stylesheet and in nine component `<style>` blocks. Redefining their
values rather than renaming them keeps this item to one file plus the five hardcoded-colour sites
below, which is the difference between a reviewable diff and a rewrite.

Component edits, each a one-line colour swap:

| File | Change |
|---|---|
| [web/src/styles/app.css](web/src/styles/app.css) `.sidebar-mark` | `background: var(--mark-bg); color: var(--mark-fg)` |
| [web/src/routes/highlights/+page.svelte](web/src/routes/highlights/+page.svelte) | same two tokens |
| [web/src/routes/search/+page.svelte](web/src/routes/search/+page.svelte) | same two tokens |
| [web/src/lib/components/ScreenshotPanel.svelte](web/src/lib/components/ScreenshotPanel.svelte) | SVG overlay `fill="var(--mark-fill)"`, `stroke="var(--mark-stroke)"`, and the `.highlight-text` block to `--mark-bg` / `--mark-fg` |
| [web/src/lib/components/ReadingCalendar.svelte](web/src/lib/components/ReadingCalendar.svelte) | `.cal-cell.l1` to `.l4` use `--heat-1` to `--heat-4`, and the empty cell uses `--heat-0` |
| [web/src/lib/components/TbrRow.svelte](web/src/lib/components/TbrRow.svelte) | `.status-reading { color: var(--link) }` |
| [web/src/routes/aliases/+page.svelte](web/src/routes/aliases/+page.svelte) | unresolved heading to `var(--warn)`, `.linked` to `var(--ok)` |
| [web/src/routes/log/+page.svelte](web/src/routes/log/+page.svelte) | `.finish-star { color: var(--warn) }` |

The light theme is applied by setting `data-theme` on `<html>`. Add a four-line toggle to
[web/src/lib/components/FontSizeControl.svelte](web/src/lib/components/FontSizeControl.svelte),
which already owns the `html` element's `zoom` and already persists a preference, rather than
adding a second settings component. Default to following `prefers-color-scheme` when no preference
is stored.

The standalone log viewer at `/logs` is a self-contained HTML string in
`_LOGS_PAGE` in [xteink_service/api.py](xteink_service/api.py) with its own inline palette. Leave
it alone. It is served directly by FastAPI with no build step precisely so it still works when the
SvelteKit build is broken, and coupling it to the app's tokens would defeat that.

#### Backfill / migration

None. CSS has no persisted state. The only stored value is the new `data-theme` preference in
`localStorage`, which is absent on first load and falls back to `prefers-color-scheme`.

#### Risks

- **`oklch()` browser support**, as in 1.5. Chrome 111, Safari 15.4, Firefox 113. The Capacitor
  Android WebView is the one to check. If it fails, every surface in the app goes transparent at
  once, which is loud rather than subtle, and the fix is to ship a hex fallback block before the
  `oklch()` block so the cascade picks the last supported declaration.
- **Perceived lightness shifts.** OKLCH lightness is perceptual, so `oklch(29% ...)` is not the
  same visual weight as `#363a4f`. The interface will look slightly different even where the intent
  is "unchanged". Tune the eight ramp percentages against a screenshot of the current build rather
  than by converting the hex values literally.
- **Alpha syntax.** `oklch(L C H / A)` needs the slash form. Writing
  `rgba`-style commas silently invalidates the declaration.
- **Scope creep.** This item is a colour refactor. Do not also change spacing, radii, fonts, or
  the responsive breakpoints while in the file.

---

## Testing plan

All Python tests run inside Docker, per [AGENT.md](AGENT.md) and the pattern in
[TESTING.md](TESTING.md). Tests use pytest with `asyncio_mode = "auto"` from
[pyproject.toml](pyproject.toml), and [conftest.py](conftest.py) pins `STATE_DB` and `KOREADER_DB`
to session-scoped temp files before any import.

```bash
docker build -t xteink-service:dev .
docker run --rm xteink-service:dev python -m pytest -v
```

### 1.1 FTS5 search

New file `tests/test_search_index.py`, following the fixture style of
[tests/test_api.py](tests/test_api.py) (a `tmp_path` database, `monkeypatch.setenv`, and hand-built
tables) rather than the module-level `TestClient` style of
[tests/test_clippings.py](tests/test_clippings.py), because these tests need a clean index per
test.

- `test_fts5_module_is_available` asserts `CREATE VIRTUAL TABLE ... USING fts5` succeeds, so a
  build on an image without FTS5 fails with a clear message instead of a confusing 500.
- `test_init_db_is_idempotent` calls `init_db` twice on the same connection with no error.
- `test_rebuild_indexes_all_five_kinds` seeds one screenshot with OCR text and a note, one
  highlight, one clipping, and one writing entry with a ref, then asserts `rebuild()` returns
  `{"screenshot": 1, "note": 1, "highlight": 1, "clipping": 1, "writing": 1}`.
- `test_rebuild_is_idempotent` runs `rebuild()` twice and asserts identical counts and an identical
  `SELECT COUNT(*) FROM search_docs`.
- `test_rebuild_skips_missing_tables` drops `clippings` and asserts `skipped == ["clipping"]` with
  the other three still indexed.
- `test_title_outranks_body` indexes a screenshot titled "Pastoral" with unrelated OCR text and
  another whose OCR text mentions "pastoral" once, then asserts the title match sorts first.
  This is the test that catches the bm25 sign error.
- `test_snippet_marks_the_match` asserts the returned snippet contains `<mark>` around the term.
- `test_snippet_escapes_html` indexes a note containing `<script>alert(1)</script>` and asserts the
  returned snippet contains `&lt;script&gt;` and exactly the `<mark>` tags, nothing else unescaped.
- `test_facets_count_all_kinds_regardless_of_filter` asserts the facet dictionary is unchanged when
  `kinds=highlight` is passed, while `results` contains only highlights.
- `test_to_match_query` is parametrised over `"`, `*`, `^foo`, `NEAR(a b)`, `a AND`, `!!!`, and
  `  `, asserting none raises and that an all-punctuation query returns `None`.
- `test_malformed_query_returns_empty_not_500` drives the endpoint with each of the above.
- `test_update_screenshot_reindexes` PUTs a new `ocr_corrected`, then asserts the old text no
  longer matches and the new text does.
- `test_delete_clipping_removes_from_index` tombstones a clipping and asserts it leaves the index.
- `test_book_filter_scopes_results` indexes two books with the same word and filters by slug.
- `test_search_db_path_is_not_captured_at_import` imports `xteink_service.search_index`, changes
  `STATE_DB` with `monkeypatch`, and asserts the module reads the new path. This is the guard
  against the import-time capture problem [conftest.py](conftest.py) documents.

The writing kind, in the same file. These tests hand-build `writing_entries` and `writing_refs`
in the fixture, exactly as `tests/test_api.py` hand-builds `synced_screenshots`, so they do not
wait on 2.5:

- `test_search_finds_a_phrase_that_exists_only_in_a_snapshot` seeds an entry whose `body` is
  `"The line that stayed with me: ((ref:1))"` and a ref whose `snapshot` contains
  `"the pastoral mode of attention"`, with that phrase present nowhere in the entry body and in no
  other row, then searches for it and asserts the entry comes back with `kind: "writing"`. **This
  is the reason the kind exists and must not be skipped.**
- `test_search_finds_both_the_clipping_and_the_entry_that_cites_it` seeds the clipping as well and
  asserts both hits are returned for one query, with `facets` reporting one of each.
- `test_anchor_token_is_not_indexed` asserts a search for `ref` does not return the entry, proving
  the literal `((ref:N))` never reaches the index.
- `test_unresolvable_anchor_is_dropped` seeds a body citing an id with no `writing_refs` row and
  asserts the entry still indexes, with the token absent from `search_docs.body`.
- `test_reindex_after_a_snapshot_changes` indexes an entry, rewrites the ref's `snapshot`, calls
  `index_writing_for_ref(conn, ref_id)`, then asserts the old snapshot text no longer matches and
  the new text does. This is the freshness contract for a ref edit.
- `test_source_state_flip_does_not_change_the_indexed_body` records `search_docs.body` for an
  entry, flips the ref to `source_state='deleted'`, and asserts the stored body is byte-identical,
  proving the no-reindex row of the freshness table.
- `test_entry_title_does_not_outrank_a_body_match` seeds an entry titled with a term and a clipping
  whose text contains the same term several times, then asserts the clipping ranks first. This is
  the test that catches someone promoting the entry title into the `book_title` column.
- `test_soft_deleted_entry_leaves_the_index` sets `deleted_at`, calls `index_writing`, and asserts
  the doc is gone; restoring and calling it again brings the doc back.
- `test_writing_kind_appears_in_facets_zero_filled` asserts `facets["writing"] == 0` on a database
  with no `writing_entries` table at all, and that the reindex response lists `writing` in
  `skipped`.
- `test_expand_anchors_is_pure` is parametrised over an empty body, a body with no anchors, an
  anchor at the very start and at the very end, two anchors in a row, the same anchor twice, and an
  id that does not resolve.

Frontend, in [web/src/lib/api.test.ts](web/src/lib/api.test.ts):

- `api.search` builds `/api/search?q=...&kinds=screenshot,note&limit=50&offset=0`.
- `api.search` with no options omits `kinds` and `book`.
- `api.reindexSearch` POSTs to `/api/search/reindex`.
- New `web/src/routes/search/search.test.ts` with `@testing-library/svelte`: rendering a response
  with a two-kind facet shows two chips with counts; clicking a chip calls the toggle handler; a
  hit with `screenshot_id: null` renders a book link and no `<img>`; a response carrying all five
  facet keys renders a chip for `writing` with its count.

Manual verification:

```bash
# 1. FTS5 is present (run this first, before writing code)
docker run --rm xteink-service:dev python -c "import sqlite3; sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(a)'); print('ok')"

# 2. Index size before and after
docker compose exec xteink-service ls -l /data/state/state.db
curl -s -X POST localhost:8090/api/search/reindex | python -m json.tool
docker compose exec xteink-service ls -l /data/state/state.db

# 3. Ranking and facets on real data
curl -s 'localhost:8090/api/search?q=pastoral' | python -m json.tool | head -40
curl -s 'localhost:8090/api/search?q=pastoral&kinds=clipping' | python -m json.tool

# 4. Hostile input returns 200 with an empty result set, not a 500
curl -s -o /dev/null -w '%{http_code}\n' 'localhost:8090/api/search?q=%22'
curl -s -o /dev/null -w '%{http_code}\n' 'localhost:8090/api/search?q=NEAR('

# 5. The public sync host still refuses it
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: <PUBLIC_SYNC_HOST>' localhost:8090/api/search   # expect 404
```

Regression risks:

- [tests/test_api.py](tests/test_api.py) builds `synced_screenshots`, `document_aliases`, and
  `progress_updates` by hand in its `api_env` fixture and never creates `highlights` or
  `clippings`. Any reindex triggered from those tests must survive the missing tables, which is
  what `skipped` is for. Its existing `/api/search` assertions will need rewriting for the new
  response shape.
- [tests/test_clippings.py](tests/test_clippings.py) has an autouse fixture that runs
  `DELETE FROM clippings` after every test against a session-shared database. That leaves orphaned
  `kind='clipping'` rows in `search_docs`, which will contaminate facet counts in any later test in
  the same session. **Extend that fixture to also run
  `DELETE FROM search_docs WHERE kind = 'clipping'`.**
- `tests/test_state.py` exercises `SyncState` directly. Adding `search_index.init_db` and indexing
  calls to `mark_synced`, `update_screenshot`, `add_highlight`, and `delete_highlight` changes the
  number of statements those methods run; assertions counting rows in `synced_screenshots` are
  unaffected, but any assertion on `cur.rowcount` or on connection behaviour needs rechecking.
- `tests/test_archiver.py` calls `mark_synced` through the archiver. If indexing raises there, a
  sync fails. Wrap every indexing call site in `try/except sqlite3.OperationalError` with a
  `logger.warning`, so a broken index degrades search rather than breaking archiving.

### 1.2 Export

New file `tests/test_export.py`:

- `test_md_groups_by_chapter_when_clippings_have_chapters` seeds two clippings with different
  chapters and asserts two `##` headings in chapter order.
- `test_md_groups_by_date_when_no_chapters` seeds clippings with `chapter = ''` and asserts date
  headings, newest first.
- `test_group_auto_prefers_chapter_when_any_clipping_has_one` seeds a mix.
- `test_include_filters_sections` asserts `include=highlights` produces no clipping lines and a
  `counts` dictionary with `clippings: 0`.
- `test_csv_quotes_multiline_text` seeds a clipping whose text contains `\n` and `"`, then round
  trips the output through `csv.reader` and asserts the text comes back byte-identical.
- `test_json_shape` asserts the exact top-level keys and that `groups[*].items[*]` carry
  `kind`, `id`, `text`, `chapter`, `date`.
- `test_unknown_format_returns_400` and `test_unknown_include_returns_400`.
- `test_slug_matches_both_title_forms` seeds screenshots under `The-Hidden-Keys` and an alias
  titled `Hidden Keys, The - Andre Alexis`, then exports under one slug and asserts both sides
  appear. This is the `canonical_book_slug` contract.
- `test_unknown_book_returns_404`.
- `test_soft_deleted_highlights_are_excluded` (only after 1.3 lands).

Writing entries, with `writing_entries` and `writing_refs` hand-built in the fixture:

- `test_md_entry_contains_no_anchor_token` exports an entry citing two refs and asserts the string
  `((ref:` does not appear anywhere in the output. **This is the self-contained-file contract and
  must not be skipped.**
- `test_md_clipping_ref_renders_as_a_blockquote` asserts the snapshot appears on a `> ` line
  followed by a `` `cited clipping` `` attribution line.
- `test_md_screenshot_ref_renders_as_an_embed` asserts the output contains
  `![[Pastoral/2026-07-04-01.png]]` and the snapshot as a blockquote caption beneath it.
- `test_deleted_ref_still_exports_its_snapshot` sets `source_state='deleted'` and asserts the
  snapshot text is present in all three formats and that the Markdown attribution line ends with
  ` - source deleted`.
- `test_drifted_ref_is_marked` asserts the ` - source changed since citation` suffix.
- `test_unresolvable_anchor_is_dropped_on_export` mirrors the search-index test, asserting the
  token does not survive into any format.
- `test_json_carries_both_body_and_body_expanded` asserts `body` still holds the anchors, that
  `body_expanded` holds none, and that `refs` is ordered by first appearance.
- `test_csv_flattens_an_entry_to_one_row` asserts a single `kind=writing` row whose `text`
  round trips through `csv.reader` unchanged and contains the expanded snapshot.
- `test_chapter_mode_puts_writing_first_not_unchaptered` asserts the `"Writing"` group is the first
  group and that `"Unchaptered"` contains no entry.
- `test_date_mode_groups_an_entry_under_its_entry_date`.
- `test_writing_is_in_the_default_include` asserts a bare export URL contains the entry, and that
  `include=highlights,clippings,notes` does not.
- `test_export_degrades_when_the_writing_tables_are_missing` drops both tables and asserts the
  export still returns 200 with `counts.writing == 0`.

Frontend:

- [web/src/lib/api.test.ts](web/src/lib/api.test.ts): `api.books.exportUrl` builds the exact query
  string for each format, and `include` is joined with commas not repeated params.
- New `web/src/lib/components/ExportMenu.test.ts`: the popover opens on click, closes on Escape,
  and unchecking "clippings" removes it from the generated href.

Manual verification:

```bash
curl -s 'localhost:8090/api/books/pastoral/export?format=md' | head -30
curl -s 'localhost:8090/api/books/pastoral/export?format=csv' | head -5
curl -s 'localhost:8090/api/books/pastoral/export?format=json&include=clippings' | python -m json.tool | head -30
curl -s -D- -o /dev/null 'localhost:8090/api/books/pastoral/export?format=md' | grep -i content-disposition
```

Then open `/books/<a book with clippings>` in the browser, use the export menu to download the
Markdown, and paste it into Obsidian to confirm the blockquotes render.

Regression risks: none expected. This item adds a read-only endpoint and touches no existing query.
The only shared code is `canonical_book_slug`, which is unchanged.

### 1.3 Soft delete, undo, trash, restore

New file `tests/test_trash.py`:

- `test_delete_highlight_is_soft` deletes and asserts the row still exists with a non-null
  `deleted_at`.
- `test_deleted_highlight_disappears_from_all_three_list_endpoints` checks `/api/highlights`,
  `/api/books/{slug}/highlights`, and `/api/screenshots/{id}/highlights`.
- `test_restore_highlight_brings_it_back` and
  `test_restore_a_live_highlight_returns_404`.
- `test_purge_requires_confirm` asserts 400 without `confirm=1`.
- `test_purge_refuses_a_live_highlight` asserts the `deleted_at IS NOT NULL` guard, so a purge
  route cannot be used to hard-delete something that is not in the trash.
- `test_delete_highlight_strips_the_vault_mark` writes a real note containing `==text==`, deletes,
  and asserts the file no longer contains `==`. This is the bug fix, so it gets its own test.
- `test_restore_highlight_rewrites_the_vault_mark`.
- `test_unmark_is_a_noop_when_the_note_was_edited` asserts `unmarked_vault: false` and no
  exception.
- `test_delete_clipping_preserves_text_in_trashed_text` asserts `text = ''` and
  `trashed_text` holds the body.
- `test_clipping_wire_shape_unchanged_for_tombstone` asserts the device-facing
  `GET /api/v1/clippings/{doc}` still returns exactly `{"id", "deleted", "revision"}` for a
  tombstone, with no `trashed_text` leaking onto the wire. **This is the device contract test and
  must not be skipped.**
- `test_restore_clipping_takes_a_fresh_higher_revision` asserts the new revision strictly exceeds
  the delete revision.
- `test_pagination_still_strictly_advances_across_a_delete_and_restore` walks the cursor with
  `limit=1` through a sequence of upload, delete, restore and asserts the cursor never moves
  backwards and strictly advances whenever items are returned, mirroring
  `test_pagination_matches_device_expectations` in
  [tests/test_clippings.py](tests/test_clippings.py).
- `test_purge_clipping_keeps_the_row` asserts `SELECT COUNT(*) FROM clippings` is unchanged and
  `trashed_text` is now empty. This is the resurrection guard.
- `test_old_tombstone_is_not_restorable` seeds a row with `deleted = 1` and `trashed_text = ''` and
  asserts a 409.
- `test_device_tombstone_appears_in_trash_with_source_device` PUTs `{"id": ..., "deleted": 1}` and
  asserts `deleted_source == "device"`.
- `test_device_reupload_clears_the_trash_state`.

Citations, in the same file, with `writing_entries` and `writing_refs` hand-built in the fixture:

- `test_deleting_a_cited_clipping_flips_the_ref_and_leaves_the_body_alone` deletes a clipping cited
  by an entry and asserts `writing_refs.source_state == 'deleted'`, that
  `writing_entries.body` is byte-identical to before, and that `writing_refs.snapshot` still holds
  the text. **This is the "deleting a passage must not gut the writing" contract and must not be
  skipped.**
- `test_undo_flips_the_ref_back_to_live` undoes that delete and asserts `source_state == 'live'`,
  and that `writing_refs_restored` counts it.
- `test_restore_from_trash_flips_the_ref_back` does the same through
  `POST /api/clippings/{id}/restore` rather than through undo, since the two paths flip separately.
- `test_delete_does_not_overwrite_a_drifted_ref` seeds a ref at `source_state='drifted'`, deletes
  the clipping, and asserts the ref is still `drifted`; undoing then leaves it `drifted` too. This
  is the pair of guards on the two `UPDATE` statements.
- `test_deleting_a_cited_highlight_flips_its_refs` is the same assertion for `kind='highlight'`.
- `test_purging_a_clipping_keeps_the_snapshot` deletes, purges, and asserts
  `writing_refs.snapshot` is unchanged and the ref stays `deleted`.
- `test_citation_counters_are_zero_without_the_writing_tables` drops both tables and asserts a
  delete still returns 200 with `writing_refs_marked: 0`.
- `test_trash_lists_soft_deleted_writing_entries` seeds an entry with `deleted_at` set and asserts
  it appears in the `writing` bucket with `counts.writing == 1`, a trimmed `excerpt`, a `ref_count`,
  and `restorable: true`.
- `test_trash_returns_an_empty_writing_bucket_without_the_table` asserts `"writing": []` and
  `counts.writing == 0` rather than a 500.
- `test_empty_trash_reports_writing_zero_when_the_table_is_missing`.
- `test_toast_label_names_the_citations` asserts the exact appended clause for one citation and
  for two.

New file `tests/test_undo.py`, covering the undo log on its own:

- `test_undo_log_init_is_idempotent` calls `undo.init_db()` twice with no error.
- `test_undo_module_reads_state_db_inside_a_function` asserts `xteink_service.undo` has no
  module-level `STATE_DB` binding, mirroring the same assertion for `search_index`.
- `test_every_destructive_route_returns_an_undo_token` asserts a non-null `undo_token` on
  `DELETE /api/highlights/{id}` and `DELETE /api/clippings/{id}`, and a literal `null` on all three
  purge routes.
- `test_undo_is_idempotent` posts the same token twice. The first call returns
  `{"already_undone": false}`, the second returns HTTP 200 with `{"already_undone": true}`, and the
  clipping's revision is asserted to have moved exactly once across both calls. **This is the
  double-click guard and must not be skipped.**
- `test_unknown_undo_token_returns_404`.
- `test_expired_undo_token_returns_410` records an entry with `ttl_s=-1` (or backdates
  `expires_at`), asserts 410, and then asserts the item is still restorable from `/trash`, so an
  expired toast never means lost data.
- `test_undo_of_an_unregistered_kind_returns_409` records a row with a made-up `kind`.
- `test_undo_clipping_delete_allocates_a_new_revision` asserts the revision after the undo is
  strictly greater than the delete revision recorded in `payload`, and that it does not reuse the
  revision the clipping held before the delete.
- `test_pagination_contract_survives_an_undo` walks `GET /api/v1/clippings/{doc}` with `limit=1`
  across upload, delete, and undo, asserting the cursor never moves backwards, strictly advances on
  every page that returns items, and that `more` is never `true` on an empty page. The restored
  clipping must appear as a fresh item above the cursor the device last held. This is the device
  contract test for the undo path and pairs with
  `test_pagination_still_strictly_advances_across_a_delete_and_restore`.
- `test_undo_highlight_delete_restores_the_vault_marks` writes a real note, deletes the highlight,
  asserts the `==` marks are gone, undoes, and asserts `==text==` is back in the file.
- `test_undo_after_reload_via_recent` deletes, discards the token the client held, calls
  `GET /api/undo/recent?limit=5`, and undoes with the token from that response.
- `test_recent_excludes_undone_and_expired_entries`.
- `test_toast_label_differs_by_deleted_source` asserts the exact `label` string for a web-UI
  delete and for a device PUT tombstone, including the "sends it back at the next sync" sentence
  on the device one.
- `test_device_tombstone_batch_records_one_undo_entry` PUTs three tombstones in one request and
  asserts a single `undo_log` row whose label counts three clippings.
- `test_undo_skips_a_purged_target` deletes, purges, then undoes, asserting HTTP 200 with the id in
  `result["skipped"]` and nothing restored.
- `test_sweep_keeps_recently_undone_rows` asserts that calling `init_db()` right after an undo does
  not delete the row, so a repeat call still answers `already_undone` rather than 404.

Frontend:

- [web/src/lib/api.test.ts](web/src/lib/api.test.ts): every purge wrapper appends `confirm=1`;
  `api.trash.get()` hits `/api/trash`; `api.undo.run(token)` POSTs `{ token }` to `/api/undo`; and
  `api.undo.recent(5)` hits `/api/undo/recent?limit=5`.
- New `web/src/routes/trash/trash.test.ts`: all three buckets render with counts; an empty bucket
  renders no section at all; the purge button opens a confirmation and does not call the API until
  confirmed; a clipping with `restorable: false` renders its Restore button disabled.
- New `web/src/lib/components/UndoToast.test.ts`: renders nothing when there is no entry; renders
  `label` verbatim; clicking Undo calls `api.undo.run` exactly once and disables the button while
  busy; a device-sourced entry renders the longer label including the next-sync sentence.

Manual verification:

```bash
# Soft delete and restore round trip
curl -s -X DELETE localhost:8090/api/highlights/41
grep -c '==' "$VAULT/Books/Pastoral.md"           # expect one fewer than before
curl -s localhost:8090/api/trash | python -m json.tool
curl -s -X POST localhost:8090/api/highlights/41/restore
grep -c '==' "$VAULT/Books/Pastoral.md"           # expect the original count

# Undo round trip, including the second call
TOKEN=$(curl -s -X DELETE localhost:8090/api/clippings/184 | python -c 'import sys,json;print(json.load(sys.stdin)["undo_token"])')
curl -s localhost:8090/api/undo/recent | python -m json.tool
curl -s -X POST localhost:8090/api/undo -H 'Content-Type: application/json' -d "{\"token\":\"$TOKEN\"}"
curl -s -X POST localhost:8090/api/undo -H 'Content-Type: application/json' -d "{\"token\":\"$TOKEN\"}"   # already_undone
docker compose exec xteink-service sqlite3 /data/state/state.db \
  "SELECT token, kind, label, undone_at FROM undo_log ORDER BY created_at DESC LIMIT 5;"

# Purge gating
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE localhost:8090/api/trash/highlights/41        # 400
curl -s -X DELETE 'localhost:8090/api/trash/highlights/41?confirm=1'                             # 200

# Row retention for clippings
docker compose exec xteink-service sqlite3 /data/state/state.db \
  "SELECT id, deleted, deleted_source, length(text), length(trashed_text) FROM clippings WHERE deleted = 1;"
```

Then, with the reader: delete a clipping in the web UI, restore it, put the X4 into File Transfer
mode, run a clipping sync, and confirm the excerpt reappears on the device. Separately, delete a
clipping on the device, confirm it lands in `/trash` with source `device`, and leave it purged to
confirm it does not resurrect on the following sync. Finally, delete a clipping on the device, open
the web UI so the toast rehydrates from `/api/undo/recent`, press Undo, and sync again to confirm
the excerpt comes back on the reader.

Regression risks:

- [tests/test_clippings.py](tests/test_clippings.py) asserts the exact wire shape in
  `test_upload_then_download_roundtrip`. Adding columns must not change `_row_to_item()`. If that
  test starts failing, a column has leaked onto the wire.
- `upload_clippings()` now writes to `undo_log` inside the same request. It must stay inside the
  existing `BEGIN IMMEDIATE` and must not change the response, which the device parses strictly for
  `accepted: 1`. If the PUT tests start failing, the undo write has escaped its transaction or
  added a key.
- `tests/test_state.py` covers `delete_highlight` returning `True`/`False`. The soft delete must
  preserve that contract: `True` when a live row was deleted, `False` otherwise, including when the
  row was already soft-deleted.
- [tests/test_vault_writer.py](tests/test_vault_writer.py) and `tests/test_vault_integration.py`
  cover the writer. Moving the mark logic out of `api.create_highlight` into `VaultWriter` adds two
  methods to a class those tests instantiate; confirm nothing there asserts the exact method set.
- The sidebar in [web/src/routes/+layout.svelte](web/src/routes/+layout.svelte) calls
  `api.highlights.listAll(5)` on mount. With soft delete it must stop showing deleted highlights,
  which happens automatically once `list_all_highlights` filters, but it is worth eyeballing.

### 1.4 On this day and review

New file `tests/test_review.py`:

- `test_on_this_day_matches_month_and_day_across_years` seeds screenshots on `2024-11-07`,
  `2025-11-07`, `2026-11-07`, and `2025-11-08`, queries `date=2026-11-07`, and asserts two year
  buckets (2025 and 2024) in that order and no 2026 entry.
- `test_on_this_day_empty_returns_zero_not_404`.
- `test_on_this_day_excludes_zero_epoch_clippings` seeds `created_at = 0` and asserts it never
  appears, regardless of the queried date.
- `test_on_this_day_bad_date_returns_400`.
- `test_on_this_day_degrades_when_a_table_is_missing` drops `clippings` and asserts the other kinds
  still return.
- `test_review_random_is_stable_for_a_seed` calls twice with the same `seed` and asserts identical
  ordering, then with a different seed and asserts a different ordering (over enough candidates
  that a collision is improbable).
- `test_review_spaced_prefers_never_shown` seeds one reviewed item with a future `next_due` and one
  never-shown item, and asserts only the never-shown one is returned.
- `test_review_spaced_excludes_items_not_yet_due`.
- `test_mark_seen_good_doubles_the_interval` asserts the 1, 2, 4, 8 progression and the 365 cap.
- `test_mark_seen_again_resets_the_interval_to_one`.
- `test_mark_seen_is_an_upsert` calls it on an item with no `review_log` row and then again.
- `test_review_excludes_soft_deleted_highlights` (after 1.3).
- `test_review_log_init_is_idempotent`.

Frontend:

- [web/src/lib/api.test.ts](web/src/lib/api.test.ts): `api.onThisDay()` omits `date` when not
  given; `api.review({ strategy: 'random', seed: '2026-11-07' })` builds the right query;
  `api.reviewSeen('highlight', 41, 'good')` POSTs the right body.
- New `web/src/lib/components/OnThisDay.test.ts`: renders nothing when `total` is 0; renders one
  section per year with a correct "N years ago" caption.
- New `web/src/routes/review/review.test.ts`: shows one item at a time; clicking "Good" calls the
  seen endpoint and advances; the empty queue shows the completion state.

Manual verification:

```bash
curl -s 'localhost:8090/api/on-this-day' | python -m json.tool
curl -s 'localhost:8090/api/on-this-day?date=2025-07-04' | python -m json.tool
curl -s 'localhost:8090/api/review?strategy=random&limit=3' | python -m json.tool
curl -s 'localhost:8090/api/review?strategy=spaced&limit=3' | python -m json.tool
curl -s -X POST localhost:8090/api/review/highlight/41/seen -H 'Content-Type: application/json' -d '{"rating":"good"}'
docker compose exec xteink-service sqlite3 /data/state/state.db "SELECT * FROM review_log;"
```

Then open `/books` and confirm the "On this day" block appears only when there is history, and walk
`/review` through five items confirming each advances and the intervals grow in `review_log`.

Regression risks:

- `GET /api/books` is already the slowest list endpoint (it groups every screenshot row and every
  progress row). Adding an `/api/on-this-day` call to the same `load()` in
  [web/src/routes/books/+page.ts](web/src/routes/books/+page.ts) doubles that page's request count.
  Fire them concurrently with `Promise.all`, as
  [web/src/routes/books/[book]/+page.ts](web/src/routes/books/%5Bbook%5D/+page.ts) already does.
- `review_log` rows reference `(kind, ref_id)` with no foreign key. A purged highlight leaves a
  stale row. Harmless (the join simply finds nothing) but the spaced candidate query must be a
  join from the source table outward, not from `review_log` inward, or purged items would be
  returned with no text.

### 1.5 Placeholder covers

New file `web/src/lib/placeholder.test.ts`:

- `hashSlug` is deterministic: the same input returns the same number across calls.
- `hashSlug` differs for `the-hidden-keys` and `the-hidden-key`.
- `coverStyle` returns a palette from the known set and `ruleCount` within 2 to 4 for a hundred
  generated slugs.
- `coverStyle('')` does not throw and returns a valid style.

New file `web/src/lib/components/PlaceholderCover.test.ts`:

- Renders an `<svg>` with `role="img"` and an `aria-label` equal to the title when given, and to
  the slug when not.
- The same slug rendered twice produces identical markup.
- Two different slugs produce different `fill` attributes (over a handful of pairs, since palette
  collisions are expected at eight entries).

Manual verification: open `/books` and confirm every card has a cover, that the covers are visually
distinct, and that reloading does not change any of them. Then open the Capacitor Android build on
the real device and confirm the covers render rather than coming out transparent, which is the
`oklch()` support check.

Regression risks: `web/src/routes/books/books.test.ts` does not exist today, so nothing asserts the
`BookCard` markup. Adding an `<svg>` inside the card changes its DOM; if a test is added for 1.5 it
should assert the cover is present, not the exact SVG contents, which will change whenever the
palette is tuned.

### 1.6 OKLCH tokens

CSS has no unit tests and none should be invented for it. Verification is visual and mechanical:

```bash
# No hardcoded colour survives outside the token block and the /logs page
cd web && grep -rnE '#[0-9a-fA-F]{3,8}\b|rgba?\(' src/ --include='*.svelte' --include='*.css' \
  | grep -v 'src/lib/assets/favicon.svg'
```

Expected after the change: no matches, or only matches inside the `:root` fallback block if an
`oklch()` fallback was added. The same command run today returns the twenty-three matches listed in
the Why section above, so this is a measurable before and after.

```bash
cd web && npm run check && npm run build
```

Manual verification: take a screenshot of `/books`, `/log`, `/clippings`, and a book detail page
before the change, apply it, and compare. Specifically check the reading heatmap cells still have
four distinguishable levels, the highlight marks are still legible in both the sidebar and the
screenshot panel overlay, and the two responsive breakpoints in
[web/src/styles/app.css](web/src/styles/app.css) (portrait under 768px, landscape under 500px
height) still lay out correctly. Then toggle light mode and repeat.

Regression risks:

- Nine component `<style>` blocks reference the renamed-in-value, same-in-name tokens. If a token
  name is accidentally dropped, the affected rule falls back to the inherited or initial value,
  which usually means black text on black. Grep for each of the nine names after the rewrite and
  confirm every one is still defined.
- [web/src/lib/components/FontSizeControl.svelte](web/src/lib/components/FontSizeControl.svelte)
  manipulates `html { zoom }`, and the comment at the top of
  [web/src/styles/app.css](web/src/styles/app.css) explains that `height: 100%` rather than `100vh`
  is load-bearing under that zoom in Firefox. Adding the theme toggle to that component must not
  disturb the zoom logic.
- No existing Vitest test reads CSS, so `npm run test` cannot catch a colour regression. Say so
  rather than pretending otherwise.

---

## Acceptance criteria

**1.1 FTS5 search**

- [ ] `CREATE VIRTUAL TABLE ... USING fts5` succeeds in the built image, verified by the command in
      "FTS5 availability" and asserted by `test_fts5_module_is_available`.
- [ ] `GET /api/search` contains no `LIKE` predicate and no Python-side snippet extraction.
- [ ] A query matching a book title ranks above a query matching only OCR text, proving the bm25
      weights and the ascending sort order are both correct.
- [ ] Clippings are searchable and appear with `kind: "clipping"`.
- [ ] The response carries all five facet counts, zero-filled, independent of the `kinds` filter.
- [ ] `search_docs.kind` accepts `writing`, and a search for a phrase that exists only inside a
      `writing_refs.snapshot` returns the citing entry.
- [ ] The same query returns both the clipping holding the phrase and the entry citing it.
- [ ] No `((ref:` token reaches `search_docs.body`, and a search for `ref` returns no entry.
- [ ] Re-indexing after a ref's `snapshot` is rewritten makes the new text findable and the old
      text not findable.
- [ ] Flipping a ref's `source_state` leaves `search_docs.body` byte-identical, with no reindex.
- [ ] A writing entry's own title does not outrank its body, and `book_title` still holds the
      book's title for `kind='writing'`.
- [ ] `expand_anchors()` exists once, in
      [xteink_service/search_index.py](xteink_service/search_index.py), and 1.2 imports it rather
      than reimplementing it.
- [ ] 1.1 creates neither `writing_entries` nor `writing_refs`; a reindex before 2.5 lands reports
      `writing` in `skipped`.
- [ ] A query of `"`, `*`, `NEAR(`, or pure punctuation returns HTTP 200 with zero results.
- [ ] A note containing `<script>` renders escaped in the browser.
- [ ] `POST /api/search/reindex` run twice produces identical `search_docs` counts.
- [ ] `ensure_built()` runs at service start only when the index is empty, and no FastAPI startup
      or lifespan hook was added.
- [ ] `xteink_service/search_index.py` reads `STATE_DB` inside a function, asserted by a test.
- [ ] `/api/search` returns 404 on the `PUBLIC_SYNC_HOST` hostname.

**1.2 Export**

- [ ] `GET /api/books/{slug}/export?format=md` returns Markdown with a
      `Content-Disposition: attachment` header.
- [ ] `format=csv` round trips multiline, quote-containing clipping text through `csv.reader`
      unchanged.
- [ ] `format=json` returns the documented shape with `grouped_by`, `counts`, and `groups`.
- [ ] Grouping is by chapter when any included clipping has one, and by date otherwise.
- [ ] `include=highlights` emits no clipping content.
- [ ] An unknown `format` or `include` value returns 400, not a silent partial export.
- [ ] A book whose screenshots and alias title differ exports both sides under one slug.
- [ ] An export menu is reachable from the book page, `/clippings`, and `/highlights`.
- [ ] `include` accepts `writing`, and `writing` is part of the default set.
- [ ] An exported Markdown entry contains no `((ref:` token anywhere in the file.
- [ ] A clipping or highlight citation renders as a blockquote with a `cited` attribution line; a
      screenshot citation renders as an `![[...]]` embed with the snapshot as a caption beneath it.
- [ ] A ref with `source_state` of `deleted` still exports its snapshot, marked
      ` - source deleted`.
- [ ] `format=json` carries `body` with anchors intact, `body_expanded` with them resolved, and
      `refs` ordered by first appearance.
- [ ] `format=csv` flattens an entry to one row, and the plan states what that loses rather than
      adding columns to hide it.
- [ ] With both writing and passages included, entries come first within a group, and in `chapter`
      mode they form a leading `"Writing"` group rather than falling into `"Unchaptered"`.
- [ ] An export against a database with no writing tables returns 200 with `counts.writing` of 0.

**1.3 Soft delete, undo, trash, restore**

- [ ] `DELETE /api/highlights/{id}` leaves the row in place with `deleted_at` set.
- [ ] The same call removes the `==…==` marks from the Obsidian note, and reports
      `unmarked_vault` honestly when it could not.
- [ ] A soft-deleted highlight is absent from all three highlight list endpoints, from
      `/api/search`, and from the sidebar's recent highlights.
- [ ] `POST /api/highlights/{id}/restore` restores the row and re-marks the note.
- [ ] `DELETE /api/clippings/{id}` preserves the body in `trashed_text` while clearing `text`.
- [ ] The device-facing tombstone wire shape is byte-identical to before: `{id, deleted, revision}`
      and nothing else.
- [ ] Restore takes a strictly higher revision, and a `limit=1` cursor walk across a delete and a
      restore never moves backwards and always strictly advances when items are returned.
- [ ] Purging a clipping clears `trashed_text` and keeps the row, with
      `"row_retained": true` in the response.
- [ ] Every purge route rejects a request without `confirm=1`.
- [ ] A purge route cannot hard-delete a live highlight.
- [ ] `/trash` lists both buckets, restores work from the UI, and purge requires an explicit
      confirmation that states the clipping row is retained.
- [ ] `DELETE /api/highlights/{id}` and `DELETE /api/clippings/{id}` both return a non-null
      `undo_token`, and all three purge routes return `"undo_token": null`.
- [ ] `POST /api/undo` called twice with the same token returns HTTP 200 both times, the second
      with `"already_undone": true`, and the target is restored exactly once.
- [ ] A token past `expires_at` returns 410, and the item is still restorable from `/trash`.
- [ ] Undoing a clipping delete allocates a strictly higher `revision` than the delete did, and a
      `limit=1` cursor walk across delete then undo never moves the cursor backwards, always
      advances it when items are returned, and never reports `more: true` on an empty page.
- [ ] Undoing a highlight delete puts the `==…==` marks back into the Obsidian note.
- [ ] The toast label for a device-sourced tombstone names the reader and says the undo sends the
      clipping back at the next sync; the web-UI label does not.
- [ ] `GET /api/undo/recent` rehydrates the toast after a page reload and never returns an expired
      or already-undone entry.
- [ ] A device PUT carrying several tombstones produces one `undo_log` row, not one per item.
- [ ] Undoing something whose target was purged returns HTTP 200 with the id in `result.skipped`
      and restores nothing.
- [ ] `undo_log` and the reversal registry live in
      [xteink_service/undo.py](xteink_service/undo.py), which reads `STATE_DB` inside a function
      and contains no reference to highlights or clippings.
- [ ] `UndoToast.svelte` is mounted once in
      [web/src/routes/+layout.svelte](web/src/routes/+layout.svelte), backed by a single shared
      `web/src/lib/undo.svelte.ts` instance in the style of `web/src/lib/panel.svelte.ts`.
- [ ] Dismissing the toast leaves the token usable and the item in `/trash`.
- [ ] Deleting a clipping cited by a writing entry flips the citing `writing_refs` rows to
      `source_state='deleted'`, leaves `writing_entries.body` byte-identical, and leaves
      `writing_refs.snapshot` intact. The delete never cascades into the entry.
- [ ] Undoing that delete, and restoring from `/trash`, both flip the same refs back to `live`, and
      neither promotes a ref that was already `drifted`.
- [ ] Purging a clipping leaves its citations' snapshots intact.
- [ ] `DELETE /api/clippings/{id}` and `DELETE /api/highlights/{id}` report
      `writing_refs_marked`, and the toast label says the citations keep the text when it is
      non-zero.
- [ ] `GET /api/trash` carries a `writing` bucket listing soft-deleted entries, and returns
      `"writing": []` with `counts.writing` of 0 on a database with no `writing_entries` table.
- [ ] `writing_delete` is named in 1.3 with a fixed payload and toast label, and its reverser is
      registered from Plan 2's module with no edit to
      [xteink_service/undo.py](xteink_service/undo.py).

**1.4 On this day and review**

- [ ] `GET /api/on-this-day` returns prior-year passages for the same month and day, newest year
      first, and never the current year.
- [ ] Clippings with `created_at = 0` never appear.
- [ ] An empty result returns `{"total": 0, "years": []}` with HTTP 200.
- [ ] `GET /api/review?strategy=random&seed=X` is stable for a given seed.
- [ ] `strategy=spaced` returns never-shown items before due items and excludes items not yet due.
- [ ] `POST /api/review/{kind}/{ref_id}/seen` doubles the interval on `good`, resets it to 1 on
      `again`, and caps at 365.
- [ ] `/books` shows the "On this day" block only when there is history, and renders nothing
      otherwise.
- [ ] `/review` shows one passage at a time and advances on rating.
- [ ] `/` is still a `replaceState` redirect to `/books`, with the Android Back behaviour intact.

**1.5 Placeholder covers**

- [ ] Every card on `/books` shows a cover.
- [ ] The same book shows the same cover across reloads and across browsers.
- [ ] The cover component makes no network request and takes no backend data.
- [ ] The `<svg>` carries `role="img"` and an `aria-label`.
- [ ] Covers render correctly in the Capacitor Android build on the real device.

**1.6 OKLCH tokens**

- [ ] Every neutral surface derives from `--tint-h` and `--tint-c`; changing `--tint-h` alone
      visibly retints the whole interface.
- [ ] Light mode is produced by overriding the lightness ramp only, with hue and chroma untouched.
- [ ] The grep for hardcoded colours returns no matches outside the token block, the favicon, and
      the self-contained `/logs` page.
- [ ] The nine existing token names are still defined, so no component had to be renamed.
- [ ] The reading heatmap still has four distinguishable levels, driven by `--heat-1` to `--heat-4`.
- [ ] Both responsive breakpoints still lay out correctly in dark and light mode.
- [ ] `npm run check` and `npm run build` both pass.

**Cross-cutting**

- [ ] No new entry was added to `_PUBLIC_ALLOWED` in
      [xteink_service/koreader_sync.py](xteink_service/koreader_sync.py).
- [ ] No new Python dependency; `pyproject.toml` and `uv.lock` are unchanged.
- [ ] No `aiosqlite` import, and no `ATTACH DATABASE` anywhere.
- [ ] No new module captures a DB path at import time.
- [ ] `docker build -t xteink-service:dev . && docker run --rm xteink-service:dev python -m pytest`
      passes.
- [ ] `ruff check .` passes at line-length 100.

---

## Out of scope

Explicitly not part of Plan 1. Several of these belong to the sibling documents; the rest are
deliberate non-goals.

- **Anything from Phase 2 of [IDEAS.md](IDEAS.md).** Derived reading sessions, daily rollups, a
  `books` table, reading attempts, read status, folding `tbr_books` into a status, the statistics
  page, the writing log itself, and persisting the clipping-to-screenshot link all belong to
  `PLAN-2-READING-DATA.md`. Plan 1 owns only the three seams that have to exist before it lands:
  the `writing` search kind (1.1), the `writing` export include (1.2), and the `writing` trash
  bucket plus the `writing_delete` contract and the citation flip (1.3). It does not create
  `writing_entries` or `writing_refs`, write an editor, a reference picker, or a vault render.
- **The command palette (3.4).** It consumes `search_index` but is designed in
  `PLAN-3-POLISH.md`. Do not add palette-specific ranking, tab bounding, or action records here.
- **Real covers, metadata providers, and a provider registry (3.1, 3.2).** 1.5 produces a
  placeholder only. No Open Library cover download, no `book_metadata` table, no `locked_fields`.
- **Semantic or vector search.** No `sqlite-vec`, no embeddings. FTS5 lexical search only.
- **Fuzzy or trigram matching.** FTS5 prefix matching (`token*`) is the extent of it. No spell
  correction, no synonyms, no `NEAR` exposed to the user.
- **Pagination UI for search.** The API takes `offset`, the UI uses a single page of up to 200.
  Windowed lists and a jump rail are 3.5.
- **URL-synced view state beyond `q`, `kinds`, and `book`.** Full round-tripping of filter and sort
  state is 3.6.
- **Export of the entire archive.** `GET /api/books/{slug}/export` is per book. A whole-archive
  export would need streaming and is not requested.
- **Readwise, Hardcover, or StoryGraph push.** Phase 4 of [IDEAS.md](IDEAS.md), speculative.
- **Cleaning orphaned `==` marks already in the vault.** 1.3 stops creating new ones. Existing ones
  are a manual Obsidian search for `==`. `POST /api/vault/rebuild` does not remove them, because it
  appends rather than rewriting, and changing that is a preview-then-apply problem (3.7).
- **Restyling the `/logs` page.** It is a self-contained HTML string in
  [xteink_service/api.py](xteink_service/api.py), deliberately independent of the SvelteKit build.
- **A real dashboard at `/`.** The route stays a `replaceState` redirect to `/books` so the Android
  hardware Back button keeps working.
- **A second placeholder cover style, or a style picker.** One style.
- **Any change to the kosync protocol, the clipping sync protocol, the device-facing wire shapes,
  or the public host allowlist.**
- **Replacing the `LIKE`-based client-side filter on the book detail page.** The "Filter by OCR
  text or notes" input in
  [web/src/routes/books/[book]/+page.svelte](web/src/routes/books/%5Bbook%5D/+page.svelte) filters
  an already-loaded array in the browser. It is correct at a single book's scale and routing it
  through FTS would add a request round trip for no gain.
