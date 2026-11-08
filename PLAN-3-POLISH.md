# Plan 3: Polish and Navigation

This document expands Phase 3 of [IDEAS.md](IDEAS.md) into an implementable design. It covers nine
items: a book metadata provider registry (3.1), covers (3.2), long-job progress (3.3), a command
palette (3.4), windowed lists and a date jump rail (3.5), URL-synced view state (3.6),
preview-then-apply for destructive work (3.7), PWA packaging (3.8), and a shareable year poster
(3.9). Where Plan 1 made the archive searchable and Plan 2 gave it a reading-data model, Plan 3 is
about making a years-deep archive navigable and about making the work the service already does
visible while it happens.

**Plan numbers are not build phases.** [TESTING.md](TESTING.md) numbers the historical build phases
1 to 11 (Foundation through Clipping sync), all complete. The "Plan 1 / Plan 2 / Plan 3" numbering
in this document and its siblings ([PLAN-1-QUICK-WINS.md](PLAN-1-QUICK-WINS.md),
[PLAN-2-READING-DATA.md](PLAN-2-READING-DATA.md)) is a separate forward-looking split of the
BookOrbit audit in [IDEAS.md](IDEAS.md). Item 3.3 here has nothing to do with build phase 3.

---

## Ordering and dependencies

| Item | Status | Blocked by | Note |
| --- | --- | --- | --- |
| 3.3 Long-job progress | independent | nothing | Build first. It is the only item that makes the other long-running items observable, and 3.1, 3.2 and 3.7 all report through it. |
| 3.5 Windowing + jump rail | independent | nothing | Pure pagination over existing endpoints. |
| 3.6 URL-synced view state | independent | nothing | Frontend only. Lands cleanest before 3.5 so the window offset can live in the URL from the start. |
| 3.7 Preview-then-apply | independent | nothing | Needs 3.3 only to report progress on the apply leg, not for the preview leg. |
| 3.8 PWA | independent | nothing | Frontend and build config only. Carries the highest regression risk of any item here, see the Capacitor analysis. |
| 3.2 Covers | blocked | **Plan 2 item 2.3** (`books.cover_path`), **Plan 1 item 1.5** (`PlaceholderCover.svelte` fallback) | Can be prototyped against `document_aliases` but should not ship before `books` exists. |
| 3.1 Book metadata | blocked | **Plan 2 item 2.3** (`books` row to hang metadata on) | `book_metadata.isbn13` is also what any future external matching needs. |
| 3.4 Command palette | blocked | **Plan 1 item 1.1** (the FTS index and `GET /api/search` with `kinds` + `limit`) | Do not build palette-specific ranking; Plan 1 owns ranking. |
| 3.9 Share poster | blocked | **Plan 2 items 2.1 and 2.2** (`reading_sessions`, `reading_daily_stats`) | Last item in the whole programme. |

Recommended landing order: **3.3, 3.6, 3.5, 3.7, 3.8**, then after Plan 2 lands **3.1, 3.2**, then
after Plan 1 lands **3.4**, and finally **3.9**.

### Seams consumed from the sibling plans

- **Plan 1 item 1.1** creates `xteink_service/search_index.py` and reshapes `GET /api/search` to
  take a `kinds` facet and return ranked, snippeted results. Item 3.4 is a client of that endpoint
  and adds nothing to it except a `limit` per kind.
- **Plan 1 item 1.5** creates `PlaceholderCover.svelte`, which renders from a slug with no network
  access. Item 3.2 falls back to it whenever `cover_path` is empty or the image fails to load.
- **Plan 2 item 2.3** creates `books` in `state.db` with `cover_path TEXT NOT NULL DEFAULT ''` and
  `total_pages INTEGER`, both deliberately left empty for this plan to populate. It also creates
  the `settings` key/value table, which 3.1 uses for provider cooldown state.
- **Plan 2 items 2.1 and 2.2** create `reading_sessions` and `reading_daily_stats`, which 3.9 reads
  through the `/api/stats/*` endpoints defined there. The poster must not scan `progress_updates`.
- **Plan 2 item 2.7** (deleting and editing reading data) is a *consumer* of item 3.7 below: a book
  delete returns a dry-run plan before it cascades. 3.7 owns the preview, diff, and confirm-dialog
  pattern; 2.7 owns the cascade, the undo entry, and the sync suppression. If 2.7 lands first it
  must ship its own preview, which is wasted work, so prefer landing 3.7 before 2.7.
- **Plan 1 item 1.3** defines `undo_log`, `xteink_service/undo.py`, and `POST /api/undo`. Item 3.2
  registers no undo kind of its own: `DELETE /api/books/{slug}/cover` is trivially reversible by
  refetching. A book delete removes the cover file as part of the 2.7 cascade, which moves it to
  the vault trash rather than unlinking it.

---

## Conventions inherited from the sibling plans

These are not restated per item. They apply to everything below.

**Database placement.** All new tables live in `state.db` (env `STATE_DB`). No `ATTACH DATABASE`.
Cross-database reads stay Python-side via the existing `_state_conn()` / `_koreader_conn()` helpers
in [xteink_service/api.py](xteink_service/api.py). Plan 2's "Data model decision" section carries
the full reasoning; nothing in Plan 3 changes it. No item in Plan 3 reads `koreader.db` at all
except 3.9, which reads the Plan 2 rollup tables in `state.db` rather than the event log.

**Schema evolution.** No migration framework. Each new module gets an idempotent `init_db()` in the
shape of [xteink_service/clippings.py](xteink_service/clippings.py), issuing
`CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`, with any `ALTER TABLE ... ADD COLUMN`
wrapped in `try` / `except sqlite3.OperationalError`. Call `init_db()` at the top of each endpoint
that touches the table. Do not grow `SyncState.__init__`, which runs inside the kosync hot path via
`_record_client_title()`.

**No import-time DB path capture.** Every new module resolves its path inside a function:

```python
def _state_db() -> str:
    return os.getenv("STATE_DB", "/data/state/state.db")
```

Never `_DB = os.getenv("STATE_DB", ...)` at module scope. `ProgressStore` in
[xteink_service/koreader_sync.py](xteink_service/koreader_sync.py) is the one existing exception and
is the reason [conftest.py](conftest.py) has to set the environment before import. Do not add a
second one.

**Book identity.** `canonical_book_slug()` from [xteink_service/booktitle.py](xteink_service/booktitle.py)
is the book key. Every new table keys on `book_slug TEXT`.

**Routing boundary.** New routes go on `router` in [xteink_service/api.py](xteink_service/api.py)
under `/api/...`. None of them may be added to the `_restrict_public_sync_host` allowlist in
[xteink_service/koreader_sync.py](xteink_service/koreader_sync.py), which is deliberately limited to
`/syncs`, `/users`, `/health`, and `/api/v1/clippings`. The SPA catch-all also deliberately raises a
JSON 404 for unmatched `api/` paths, because CrossPoint's clipping probe parsed the old HTML shell
and reported an incompatible server. Nothing here may reintroduce an HTML fallback under `api/`.

**No em dashes** in code comments, UI copy, or documentation.

---

## Work items

### 3.1 Book metadata beyond page counts

#### Why

[xteink_service/book_pages.py](xteink_service/book_pages.py) is the only metadata path in the
service and it asks Open Library for exactly one field, `number_of_pages_median`, keyed on the
alias title. Everything else a book page could show (author, publication year, ISBN, description,
publisher) is absent, and `/api/tbr/search` in [xteink_service/api.py](xteink_service/api.py) hits
Open Library a second time with its own hand-rolled query and its own result shape. There is no
cache beyond the single `book_pages` row, no rate limiting, and no way to protect a hand-corrected
value from being overwritten.

There is also a live bug worth fixing in the same change: `search_books()` at
[xteink_service/api.py](xteink_service/api.py) is declared `async def` but calls
`urllib.request.urlopen(req, timeout=8)` directly, which blocks the event loop for up to eight
seconds and stalls every concurrent request including the X4's kosync pushes. `aiohttp>=3.9` is
already a declared dependency and already used by [xteink_service/archiver.py](xteink_service/archiver.py),
so the fix costs nothing.

#### Data model

```sql
CREATE TABLE IF NOT EXISTS book_metadata (
    book_slug       TEXT PRIMARY KEY,
    title           TEXT    NOT NULL DEFAULT '',
    author          TEXT    NOT NULL DEFAULT '',
    published_year  INTEGER,
    isbn13          TEXT    NOT NULL DEFAULT '',
    publisher       TEXT    NOT NULL DEFAULT '',
    description     TEXT    NOT NULL DEFAULT '',
    page_count      INTEGER,
    cover_url       TEXT    NOT NULL DEFAULT '',
    source          TEXT    NOT NULL DEFAULT '',
    source_ref      TEXT    NOT NULL DEFAULT '',
    locked_fields   TEXT    NOT NULL DEFAULT '[]',
    fetched_at      INTEGER NOT NULL DEFAULT 0,
    updated_at      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_book_metadata_fetched
    ON book_metadata (fetched_at);

CREATE TABLE IF NOT EXISTS provider_cache (
    provider   TEXT    NOT NULL,
    cache_key  TEXT    NOT NULL,
    response   TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT 'ok',
    fetched_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_provider_cache_fetched
    ON provider_cache (fetched_at);

CREATE TABLE IF NOT EXISTS provider_state (
    provider      TEXT PRIMARY KEY,
    last_call_at  REAL    NOT NULL DEFAULT 0,
    cooldown_until INTEGER NOT NULL DEFAULT 0,
    fail_count    INTEGER NOT NULL DEFAULT 0
);
```

`locked_fields` is a JSON array of column names, for example `["title","author"]`. A refetch never
writes a locked column. This is the one piece of BookOrbit's metadata machinery worth having at
n=1: it is what makes a hand-corrected title survive an automatic refresh.

`provider_state` lives in the database rather than in a module global because
[xteink_service/sync_once.py](xteink_service/sync_once.py) runs as a separate process from the
long-lived service. An in-memory rate limiter would be bypassed by the CLI.

**Relationship to `book_pages`.** `book_pages` stays. It is keyed on alias title and is written by
the File Transfer flow, and ripping it out would disturb `_pages_by_hash()` and the reading-log page
numbers. `book_metadata.page_count` is the richer value; `book_pages` remains the fast path.
`refresh_metadata()` writes both: `book_metadata.page_count` and, when `book_pages` has no row for
the title, a `book_pages` row with `source='metadata'`.

#### Provider interface

New package `xteink_service/metadata/`:

```
xteink_service/metadata/__init__.py       # init_db, refresh_book, refresh_stale, get_metadata
xteink_service/metadata/registry.py       # Provider protocol, PROVIDERS, throttle, cache
xteink_service/metadata/openlibrary.py
xteink_service/metadata/googlebooks.py
```

```python
class Provider(Protocol):
    name: str
    host: str                      # single allowlisted hostname
    min_interval_s: float          # minimum seconds between calls

    async def search(
        self, session: aiohttp.ClientSession, title: str, author: str
    ) -> list[Candidate]: ...
```

`Candidate` is a plain dataclass with the `book_metadata` columns plus a `confidence` float. The
registry is an ordered list; the first provider that returns a candidate above
`MIN_CONFIDENCE = 0.5` wins, and the loser's response is still cached so a later re-run is free.

Confidence is deliberately crude: normalised token overlap between the queried title and the
candidate title, times 1.0 if the author matches or 0.8 if no author is known. Do not build
BookOrbit's `candidate-relevance.ts` scoring here. One reader correcting the occasional wrong match
by hand is cheaper than maintaining a scorer.

**Query sanitisation is reused, not rewritten.** `_clean_query()` in
[xteink_service/book_pages.py](xteink_service/book_pages.py) already strips the `-` that Open
Library parses as a Lucene NOT operator and that the `"Title - Author"` filename convention makes
unavoidable. Move it to `xteink_service/metadata/registry.py` and re-export it from `book_pages` so
the existing import keeps working:

```python
# xteink_service/book_pages.py
from xteink_service.metadata.registry import clean_query as _clean_query  # noqa: F401
```

**Google Books without an API key.** `GOOGLE_BOOKS_API_KEY` is optional. When absent, call
`https://www.googleapis.com/books/v1/volumes?q=...` unauthenticated, which works but carries a much
lower per-IP quota. On HTTP 429 or 403, write `provider_state.cooldown_until = now + 3600` and skip
the provider until it expires. Never fail the caller: a missing key degrades to Open Library only.

**Outbound safety.** Each provider declares exactly one `host`. The registry builds request URLs
itself from that host and never follows a URL taken from a provider response, except cover images in
3.2, which are checked against a separate allowlist. Every call uses
`aiohttp.ClientTimeout(total=8)` and a `User-Agent` of `xteink-commonplace/1.0`. Responses are read
with a size cap (`resp.content.read(256 * 1024)`) so a hostile or broken endpoint cannot exhaust
memory. Cache TTL is 30 days for a successful lookup and 24 hours for a miss, so a book with no
match is not re-queried on every refresh.

#### API

```
GET /api/books/{slug}/metadata
  -> 200 {"book_slug": "...", "title": "...", "author": "...", "published_year": 1973,
          "isbn13": "", "publisher": "", "description": "", "page_count": 198,
          "cover_url": "", "source": "openlibrary", "source_ref": "/works/OL123W",
          "locked_fields": ["title"], "fetched_at": 1762300000}
  -> 404 {"detail": "no metadata"}   when never fetched

POST /api/books/{slug}/metadata/refresh
  body: {"force": false}
  -> 200 {"book_slug": "...", "updated_fields": ["author","published_year"],
          "skipped_locked": ["title"], "source": "openlibrary", "cached": false}
  -> 502 {"detail": "all providers failed"}

PUT /api/books/{slug}/metadata
  body: {"title": "...", "author": "...", "published_year": 1973, "isbn13": "...",
         "publisher": "...", "description": "...", "page_count": 198}
  All fields optional. Every field present in the body is written AND added to locked_fields.
  -> 200 {"book_slug": "...", "locked_fields": ["title","author"]}

DELETE /api/books/{slug}/metadata/locks/{field}
  -> 200 {"locked_fields": [...]}

POST /api/metadata/refresh-all
  body: {"only_missing": true}
  -> 202 {"job_id": 17}          # runs as a 3.3 job, see below
```

`GET /api/tbr/search` keeps its path and response shape (it is consumed by
[web/src/routes/tbr/+page.svelte](web/src/routes/tbr/+page.svelte)) but is reimplemented on top of
the registry so it shares the cache, the throttle, and the aiohttp session. This removes the
blocking `urlopen` call noted above.

#### Service changes

| File | Change |
| --- | --- |
| `xteink_service/metadata/registry.py` | new: `Provider`, `Candidate`, `clean_query()`, `cached_call()`, `throttle()`, `PROVIDERS` |
| `xteink_service/metadata/openlibrary.py` | new: search against `openlibrary.org`, reusing the existing field list plus `isbn`, `publisher`, `first_publish_year` |
| `xteink_service/metadata/googlebooks.py` | new: search against `www.googleapis.com` |
| `xteink_service/metadata/__init__.py` | new: `init_db()`, `refresh_book(slug, *, force)`, `refresh_stale(only_missing)`, `get_metadata(slug)`, `set_metadata(slug, fields)` |
| [xteink_service/api.py](xteink_service/api.py) | add the five routes above; rewrite `search_books()` on the registry |
| [xteink_service/book_pages.py](xteink_service/book_pages.py) | re-export `_clean_query` from the registry; have `_store()` also populate `book_metadata.page_count` when the row exists |
| [xteink_service/main.py](xteink_service/main.py) | after `resolve_book_pages()` in the watcher loop, call `refresh_stale(only_missing=True)` as a 3.3 job |

#### UI

- [web/src/routes/books/[book]/+page.svelte](web/src/routes/books/[book]/+page.svelte): a metadata
  block under the title showing author, year, publisher, page count, with a pencil affordance.
- New `web/src/lib/components/MetadataEditor.svelte`: inline edit per field. Saving a field locks
  it and shows a small lock icon with a click-to-unlock tooltip reading "locked, will not be
  overwritten by a refresh".
- New `web/src/lib/components/MetadataRefreshButton.svelte`: calls the refresh endpoint and
  surfaces `skipped_locked` in the result toast.
- [web/src/lib/api.ts](web/src/lib/api.ts): add `metadata.get`, `metadata.refresh`, `metadata.save`,
  `metadata.unlock` and a `BookMetadata` interface.

#### New dependencies

None. `aiohttp>=3.9` is already in [pyproject.toml](pyproject.toml).

#### Risks

- **Wrong match written confidently.** Mitigated by `MIN_CONFIDENCE`, by `source_ref` being stored
  so a bad match is traceable, and by the fact that every field is hand-correctable and then locked.
  `book_pages.py` already documents the precedent: a generic article title confidently matched an
  unrelated book and the bogus count fed `pages_read`. Carry that lesson forward by excluding
  articles here too, via `content_kind.kinds_by_hash()`, exactly as `book_pages` does.
- **Quota exhaustion.** Bounded by `min_interval_s`, the 30-day cache, and the cooldown on 429.
- **Cache growth.** `provider_cache` is one row per provider per query. For a personal library this
  is thousands of rows at most. A sweeper is not worth building; note it and move on.

---

### 3.2 Covers

#### Why

There is no cover art anywhere in the service. `/api/tbr/search` returns an Open Library
`cover_url` that is rendered directly from the remote host in the TBR page and stored nowhere, so
the one place covers appear today is a hotlink that breaks offline and leaks a request to
openlibrary.org on every render. `books.cover_path` is declared by Plan 2 item 2.3 and is empty.

#### Data model

No new table. Populates `books.cover_path` (vault-relative, for example
`Fifteen-Dogs/cover.jpg`) and adds one column:

```sql
ALTER TABLE books ADD COLUMN cover_version TEXT NOT NULL DEFAULT '';
```

wrapped in `try` / `except sqlite3.OperationalError`.

`cover_version` is the first 12 hex characters of the SHA-256 of the stored file bytes, computed at
download time. It is the cache-busting stamp. Using the content hash rather than an mtime means a
Syncthing-induced mtime change does not invalidate a cache entry, and a genuinely replaced cover
always does.

#### Where covers live

`VAULT_PATH/Books/<Title>/cover.jpg`, next to the screenshot PNGs and their JSON sidecars.

Reasons: Obsidian renders it in the book folder, which is a real benefit for the vault reader; the
existing `/api/screenshots/{id}/image` route already serves `FileResponse` from
`VAULT_PATH/Books/<vault_png_path>`, so there is an established pattern and no new storage root; and
the DB stays the source of truth because `cover_path` plus `cover_version` fully describe the file.

**`.stignore`.** Covers are **not** added to `.stignore`. They are small, they belong to the vault's
content, and excluding them would mean the Obsidian view on another machine shows a broken embed.

**Vault rebuild.** `rebuild_vault()` in [xteink_service/api.py](xteink_service/api.py) only calls
`append_to_daily_note()` and never deletes files, so covers survive a rebuild untouched. State this
in the docstring so a future change does not break it by accident.

**Re-download safety.** `fetch_cover()` is a no-op when `cover_path` points at an existing file and
`force` is false. The file is written to `cover.jpg.tmp` and `os.replace()`d into place so a
Syncthing peer never sees a half-written image.

#### Download safety

- Only `http` or `https`, and only from an allowlist: `covers.openlibrary.org`,
  `books.google.com`, `books.googleusercontent.com`.
- `aiohttp.ClientTimeout(total=15)`, maximum 2 MB read, and the body is rejected unless Pillow can
  open it and reports a format in `{"JPEG", "PNG", "WEBP"}`. Pillow is already a dependency.
- Re-encoded to JPEG at a maximum long edge of 1000 px, quality 82. This normalises the stored
  format so the serve path never has to content-sniff, and it bounds vault growth.

#### API

```
GET /api/books/{slug}/cover?v=<cover_version>
  -> 200 image/jpeg   FileResponse
       Cache-Control: public, max-age=31536000, immutable      when v matches cover_version
       Cache-Control: no-cache                                 when v is absent or stale
  -> 404 {"detail": "no cover"}

POST /api/books/{slug}/cover/fetch
  body: {"url": null, "force": false}
  url null  -> use book_metadata.cover_url from 3.1
  url given -> must pass the host allowlist, else 400
  -> 200 {"cover_path": "Fifteen-Dogs/cover.jpg", "cover_version": "a1b2c3d4e5f6", "bytes": 48213}
  -> 400 {"detail": "cover host not allowed"}
  -> 502 {"detail": "cover download failed"}

DELETE /api/books/{slug}/cover
  -> 200 {"deleted": true}     removes the file and clears cover_path/cover_version
```

`GET /api/books` gains `cover_url` per row, as a relative path
`/api/books/<slug>/cover?v=<version>` or `null`. The client never sees an external host.

#### Service changes

| File | Change |
| --- | --- |
| `xteink_service/covers.py` | new: `fetch_cover(slug, url, *, force)`, `cover_file(slug)`, `delete_cover(slug)`, `ALLOWED_COVER_HOSTS` |
| [xteink_service/api.py](xteink_service/api.py) | three routes above; add `cover_url` to `list_books()` |
| [xteink_service/metadata/__init__.py](xteink_service/metadata/__init__.py) | `refresh_book()` calls `fetch_cover()` when a `cover_url` is found and no cover is stored |

#### UI

- New `web/src/lib/components/BookCover.svelte`: renders `<img>` when `cover_url` is present,
  falls back to `PlaceholderCover.svelte` from Plan 1 item 1.5 on absence **and** on `onerror`.
  Takes `slug`, `title`, `coverUrl`, and a `size` variant.
- [web/src/lib/components/BookCard.svelte](web/src/lib/components/BookCard.svelte) and the sidebar
  book index in [web/src/routes/+layout.svelte](web/src/routes/+layout.svelte) use it.
- [web/src/routes/tbr/+page.svelte](web/src/routes/tbr/+page.svelte) stops hotlinking the Open
  Library thumbnail and uses `BookCover` instead.

#### New dependencies

None. Pillow and aiohttp are already present.

#### Risks

- **Vault growth.** One JPEG per book at a 1000 px long edge is roughly 50 to 150 KB. A thousand
  books is well under 150 MB, which is acceptable for a Syncthing share that already carries
  screenshot PNGs.
- **Wrong cover from a wrong match in 3.1.** `DELETE /api/books/{slug}/cover` plus a manual
  `POST .../cover/fetch` with an explicit URL is the escape hatch.

---

### 3.3 Long-job progress

#### Why

The service does a lot of slow work and reports none of it to the UI. `run_sync()` in
[xteink_service/archiver.py](xteink_service/archiver.py) downloads, OCRs, stitches and writes every
new screenshot; `resolve_book_pages()` in [xteink_service/book_pages.py](xteink_service/book_pages.py)
does network lookups and may download an epub per book; `_scan_resolve()` in
[xteink_service/alias.py](xteink_service/alias.py) walks the device file listing; `rebuild_vault()`
in [xteink_service/api.py](xteink_service/api.py) rewrites every note. The only visibility is the
in-memory ring buffer in [xteink_service/logbuffer.py](xteink_service/logbuffer.py) and the
hand-rolled HTML page at `/logs`, which requires the user to go and read log lines.

`logbuffer` also supplies the design precedent worth copying: `/api/logs` is an incremental
cursor feed (`?after=<last_id>`), and the jobs feed below is the same idea with an `EventSource` on
top.

#### Data model

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rev         INTEGER NOT NULL DEFAULT 0,
    kind        TEXT    NOT NULL,
    label       TEXT    NOT NULL DEFAULT '',
    state       TEXT    NOT NULL DEFAULT 'running',
    processed   INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    detail      TEXT    NOT NULL DEFAULT '',
    error       TEXT    NOT NULL DEFAULT '',
    started_at  INTEGER NOT NULL DEFAULT 0,
    ended_at    INTEGER,
    pid         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_jobs_rev     ON jobs (rev);
CREATE INDEX IF NOT EXISTS idx_jobs_state   ON jobs (state, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_started ON jobs (started_at DESC);
```

`state` is one of `running`, `done`, `failed`, `cancelled`, `stale`.

`kind` is one of `sync`, `ocr`, `page_lookup`, `alias_scan`, `vault_rebuild`, `metadata_refresh`,
`cover_fetch`.

`rev` is a globally monotonic counter assigned on every insert and every update, exactly as
`_next_revision()` does for clippings in [xteink_service/clippings.py](xteink_service/clippings.py).
It doubles as the SSE cursor, which is what makes reconnection trivial.

`total = 0` means indeterminate: the UI shows a spinner rather than a bar.

`pid` exists so a restart can mark orphans. On `init_db()`, any row with `state='running'` and
`pid != os.getpid()` is set to `state='stale'`. This is correct for both the service and the
`sync_once.py` CLI because neither reuses a pid within a run.

**Retention.** `init_db()` also deletes finished rows older than 7 days. The jobs table is a live
progress surface, not an audit log, the same posture `logbuffer.py` takes in its docstring.

#### The job context manager

```python
# xteink_service/jobs.py
@contextmanager
def job(kind: str, label: str, total: int = 0) -> Iterator[JobHandle]: ...
```

`JobHandle` exposes `advance(n=1, detail='')`, `fail_one(detail='')`, `set_total(n)`, and
`note(detail)`. It coalesces writes: an `advance()` only touches the database when more than
`WRITE_INTERVAL_S = 0.5` has passed since the last write, or when the job ends. Without this, OCR
over a hundred screenshots would be a hundred extra commits against the same file the kosync path
writes to.

The context manager always closes the job, writing `state='done'` or, on exception,
`state='failed'` with a sanitised `error`, and then re-raises. **No instrumentation may change the
behaviour of the work it wraps.**

#### API

```
GET /api/jobs?active=true&limit=20
  -> 200 {"jobs": [{"id": 17, "rev": 412, "kind": "ocr", "label": "OCR 23 screenshots",
                    "state": "running", "processed": 9, "total": 23, "failed": 0,
                    "detail": "Fifteen Dogs p.84", "started_at": 1762300000,
                    "ended_at": null}],
          "last_rev": 412}

GET /api/jobs/stream?cursor=0
  -> 200 text/event-stream
     event: job
     id: 412
     data: {"id":17,"rev":412,"kind":"ocr",...}

     event: ping
     data: {"t":1762300030}

POST /api/jobs/{id}/dismiss
  -> 200 {"dismissed": true}      # finished jobs only; sets ended_at and bumps rev
```

#### How SSE works against synchronous sqlite3

`GET /api/jobs/stream` returns a `StreamingResponse` with `media_type="text/event-stream"` and an
async generator that loops:

1. `rows = await asyncio.to_thread(_rows_since, cursor)` so the synchronous `sqlite3` read never
   blocks the event loop. This is the same discipline 3.1 applies to outbound HTTP.
2. Emit one `event: job` per row, setting the SSE `id:` field to the row's `rev`.
3. Advance `cursor` to the highest `rev` seen.
4. `await asyncio.sleep(1.0)`.
5. Every 30 seconds with no rows, emit `event: ping` to keep the proxy from closing an idle
   connection. Nginx Proxy Manager fronts this host, so the stream also sets
   `X-Accel-Buffering: no` and `Cache-Control: no-cache`.

Polling at 1 Hz rather than using an in-process `asyncio.Queue` is a deliberate choice. A queue
would only see jobs created inside the uvicorn process, and `sync_once.py` runs as a separate
process. Polling a table is the only design where both producers are visible, and a 1 Hz indexed
read of a handful of rows is not a meaningful cost.

**Multiple tabs.** Each tab opens its own generator with its own cursor. Streams are capped at
`MAX_STREAMS = 8` with a module-level counter guarded by a lock; beyond that the endpoint returns
503 and the client falls back to polling `GET /api/jobs` every 3 seconds. The cap matters because
`adapter-static` plus the Capacitor WebView makes it easy to end up with several live clients.

**Uvicorn reload and restarts.** The connection drops and the browser's `EventSource` reconnects
automatically, sending `Last-Event-ID`. The handler reads that header when `?cursor=` is absent, so
no job update is missed across a restart. On the server side, `init_db()` marks the orphaned
`running` rows `stale`, so the UI does not show a permanently spinning job.

#### Service changes

| File | Change |
| --- | --- |
| `xteink_service/jobs.py` | new: `init_db()`, `job()` context manager, `JobHandle`, `list_jobs()`, `rows_since()`, `dismiss()` |
| [xteink_service/archiver.py](xteink_service/archiver.py) | wrap `run_sync()` in `job('sync', ...)`; wrap the per-screenshot loop in `job('ocr', ..., total=len(new))` with `advance()` per file |
| [xteink_service/book_pages.py](xteink_service/book_pages.py) | wrap `resolve_book_pages()` in `job('page_lookup', ..., total=len(todo))` |
| [xteink_service/alias.py](xteink_service/alias.py) | wrap `_scan_resolve()` in `job('alias_scan', ...)` |
| [xteink_service/api.py](xteink_service/api.py) | wrap `rebuild_vault()` in `job('vault_rebuild', ..., total=len(rows))`; add the three routes |
| [xteink_service/main.py](xteink_service/main.py) | call `jobs.init_db()` at startup so orphans are swept |
| [xteink_service/sync_once.py](xteink_service/sync_once.py) | call `jobs.init_db()` before the sync cycle |

#### UI

- New `web/src/lib/jobs.svelte.ts`: a rune-based store holding `jobs: Job[]`, opening the
  `EventSource`, merging by `id`, and falling back to polling on `onerror` or 503.
- New `web/src/lib/components/JobWidget.svelte`: a floating bottom-right card, mounted once in
  [web/src/routes/+layout.svelte](web/src/routes/+layout.svelte). Collapsed by default showing
  "2 jobs running"; expands to per-job rows with a determinate bar when `total > 0`, a spinner
  otherwise, and a dismiss control on finished rows. Hidden entirely when there is nothing to show.
- [web/src/lib/components/StatusWidget.svelte](web/src/lib/components/StatusWidget.svelte) gains a
  job count badge.
- Failed jobs link to `/logs` with the `contains` filter preset to the job kind.

#### New dependencies

None. SSE is plain `StreamingResponse`; `EventSource` is a browser built-in.

#### Risks

- **Write contention with the kosync path.** Bounded by the 0.5 s coalescing interval. Worth
  stating in the module docstring so a future contributor does not remove it.
- **Instrumentation changing behaviour.** The context manager must swallow nothing. The existing
  `rebuild_vault()` already has a bare `except Exception: pass` per note, which is a separate
  problem fixed in 3.7.

---

### 3.4 Command palette

#### Why

Navigation today is the sidebar in [web/src/routes/+layout.svelte](web/src/routes/+layout.svelte)
plus a search form that navigates to `/search`. Reaching a specific passage means search, read the
result list, click through. For a personal archive the palette is the navigation, and Plan 1 item
1.1 is about to make a single ranked, snippeted, multi-kind query available.

**Blocked by Plan 1 item 1.1.** Do not build this before the FTS index exists, and do not add
palette-specific ranking. Plan 1 owns ranking.

#### Data model

None.

#### API

None new. The palette calls the Plan 1 endpoint:

```
GET /api/search?q=<query>&kinds=book,highlight,clipping,screenshot&limit=5&per_kind=true
```

`per_kind=true` is the only addition requested of Plan 1: it caps results per kind rather than
overall, so one noisy kind cannot crowd out the others. If Plan 1 has already shipped without it,
the palette issues one request per active tab instead, which is acceptable at this scale.

#### Service changes

None.

#### UI

- New `web/src/lib/components/CommandPalette.svelte`.
- New `web/src/lib/palette.svelte.ts`: a single shared `$state` instance so the sidebar button, the
  mobile nav button, and the keyboard shortcut all drive the same palette. This mirrors the existing
  single-instance pattern in [web/src/lib/panel.svelte.ts](web/src/lib/panel.svelte.ts).
- Mounted once in [web/src/routes/+layout.svelte](web/src/routes/+layout.svelte).

Behaviour:

| Aspect | Decision |
| --- | --- |
| Open | `Cmd+K` on macOS, `Ctrl+K` elsewhere, plus a sidebar button and a mobile bottom-nav button |
| Close | `Esc`, backdrop click, or selecting a result |
| Debounce | 150 ms, with the in-flight request aborted via `AbortController` on the next keystroke |
| Tabs | `all`, `books`, `highlights`, `clippings`, `pages`, `actions`; `Tab` cycles |
| Bounds | 5 results per section in `all`, 20 in a single-kind tab |
| Keys | arrows move, `Enter` activates, `Cmd+Enter` opens in a new tab for navigable results |
| Empty query | shows the action list and the five most recently touched books |

Actions are a static registry in `palette.svelte.ts`, each `{ id, label, keywords, run }`:

- Trigger a device sync (`POST /api/sync/run`, added in 3.7 alongside the dry-run work)
- Rebuild the vault (routes to the 3.7 preview, never fires the destructive call directly)
- Toggle theme (the Plan 1 item 1.6 token switch)
- Open the log viewer (`/logs`, with `data-sveltekit-reload` since it is server-rendered)
- Go to Books, Reading log, TBR, Highlights, Clippings, Aliases, Articles, Trash

Results are keyboard-first but each row is also a real link so middle-click and long-press work.

#### New dependencies

None. Ranking is server-side, the dialog is a plain `<dialog>` element, and matching for the action
list is a simple case-insensitive substring over `label` plus `keywords`.

#### Risks

- **Android WebView keyboard.** `Cmd+K` is meaningless there, which is why the mobile bottom-nav
  button is not optional.
- **`<dialog>` focus trapping** interacts badly with the virtual keyboard on some Android versions.
  Test in the APK, not only in a desktop browser.

---

### 3.5 Windowed lists and a date jump rail

#### Why

[web/src/routes/clippings/+page.svelte](web/src/routes/clippings/+page.svelte) loads 500 rows and
renders all of them; [web/src/routes/highlights/+page.svelte](web/src/routes/highlights/+page.svelte)
loads 200. Those caps exist because the client has no windowing, not because the data stops there.
`GET /api/clippings` already caps `limit` at 1000 and `GET /api/highlights` defaults to 100. An
archive that runs for years will pass both limits, and the content silently truncates with no
indication to the reader.

#### Data model

No new tables. One index each to make keyset ordering cheap:

```sql
CREATE INDEX IF NOT EXISTS idx_clippings_created
    ON clippings (created_at DESC) WHERE deleted = 0;
CREATE INDEX IF NOT EXISTS idx_highlights_created
    ON highlights (created_at DESC);
```

The `highlights` predicate gains `WHERE deleted_at IS NULL` once Plan 1 item 1.3 lands. If 1.3 has
already landed, write it that way from the start.

#### API

Both list endpoints gain `offset` and return a total:

```
GET /api/clippings?offset=0&limit=100
  -> 200 {"items": [...], "total": 4812, "offset": 0, "limit": 100}

GET /api/highlights?offset=0&limit=100
  -> 200 {"items": [...], "total": 937, "offset": 0, "limit": 100}
```

This is a **breaking response-shape change**: both endpoints currently return a bare array. Change
the client in the same commit. There is one consumer per endpoint plus the sidebar's recent
highlights, all in [web/src/lib/api.ts](web/src/lib/api.ts), so the blast radius is small and a
compatibility shim is not worth it.

New bucket endpoints for the jump rail:

```
GET /api/clippings/buckets?by=month
  -> 200 {"buckets": [{"label": "Nov 2026", "key": "2026-11", "offset": 0,   "count": 41},
                      {"label": "Oct 2026", "key": "2026-10", "offset": 41,  "count": 112}],
          "total": 4812}

GET /api/highlights/buckets?by=month
  -> same shape
```

`by` accepts `month` or `year`. `offset` is the running sum of preceding counts, which is exactly
the offset to pass to the list endpoint, so the client needs no arithmetic. The server computes
this with a single grouped query, which is the point: the client stays dumb.

#### Service changes

| File | Change |
| --- | --- |
| [xteink_service/clippings.py](xteink_service/clippings.py) | `list_clippings()` gains `offset`; new `clipping_buckets(by)`. The kosync `sync_router` is untouched: its pagination contract is dictated by the firmware and must not change. |
| [xteink_service/api.py](xteink_service/api.py) | `list_highlights()` gains `offset` and `total`; new `/api/highlights/buckets`; both list responses become envelopes |

Explicit warning for the implementer: the device-facing
`GET /api/v1/clippings/{document}` pagination contract (cursor never moves backwards, cursor
strictly advances when items are returned, no `more: true` on an empty page) is validated strictly
by the firmware. The UI endpoints in this item are a different surface. Do not refactor them into a
shared paginator.

#### UI

- New `web/src/lib/window.svelte.ts`: `createWindow({ fetchPage, pageSize })` returning
  `{ items, total, ensureRange(start, end) }`. `items` is a sparse array of `T | null`; `null` is a
  slot that has not been fetched. In-flight pages are tracked so a scroll that crosses a boundary
  twice does not fetch twice.
- New `web/src/lib/components/WindowedList.svelte`: renders `total` rows, each either the real
  component or a fixed-height skeleton, with an `IntersectionObserver` sentinel every `pageSize`
  rows calling `ensureRange`. Fixed-height skeletons are what make the scrollbar honest without
  measuring anything.
- New `web/src/lib/components/JumpRail.svelte`: a vertical rail of bucket labels, fixed on desktop
  and a horizontal scroller above the list on mobile. Clicking scrolls to `offset * rowHeight` and
  calls `ensureRange`.
- [web/src/routes/clippings/+page.svelte](web/src/routes/clippings/+page.svelte) and
  [web/src/routes/highlights/+page.svelte](web/src/routes/highlights/+page.svelte) adopt both and
  drop their hard caps.

Deliberately **not** virtualised. Rendering `total` lightweight skeleton divs is fine into the tens
of thousands and keeps native browser find-in-page, anchor links, and scroll restoration working.
If the archive ever outgrows that, revisit with a real virtualiser.

#### New dependencies

None. `IntersectionObserver` is a browser built-in.

#### Risks

- **Variable row heights.** `ClippingCard` height depends on text length, so a uniform skeleton
  height makes the scrollbar approximate. Accepted: the jump rail is the precise control and the
  scrollbar is the rough one.
- **The envelope change.** Covered by updating the three call sites in the same commit and by the
  Vitest cases below.

---

### 3.6 URL-synced view state

#### Why

`/search` already reads `q` from the URL, but `/clippings`, `/highlights`, and the tab selection on
`/books/[book]` keep their state in component memory. Reloading loses the tab, a filtered view
cannot be linked or bookmarked, and the Android hardware Back button does not undo a tab change.
That last point matters more here than in a desktop app: the root route
[web/src/routes/+page.svelte](web/src/routes/+page.svelte) already uses `replaceState` specifically
to keep hardware Back able to exit the app.

#### Data model

None.

#### API

None.

#### Service changes

None.

#### UI

New `web/src/lib/urlstate.ts`:

```ts
export function syncParam(
  key: string,
  initial: string,
  opts?: { replace?: boolean }
): { value: string }
```

Backed by `$page.url.searchParams` for reads and `goto()` for writes, always with
`{ keepFocus: true, noScroll: true, replaceState: opts?.replace ?? true }`.

Parameters per route:

| Route | Params |
| --- | --- |
| `/search` | `q`, `notes_only`, `kinds` (from Plan 1 item 1.1) |
| `/clippings` | `offset`, `book` |
| `/highlights` | `offset`, `book` |
| `/books/[book]` | `tab` (`screenshots` \| `highlights` \| `clippings` \| `log` \| `writing`) |
| `/trash` (Plan 1 item 1.3) | `bucket` |

Rule: **filter and tab changes use `replaceState`; navigation to a different record uses a push.**
Otherwise typing in a search box fills the history stack and Back becomes unusable, which on Android
means the user cannot leave the app.

**One exception, and it matters.** The `writing` tab holds a composer with unsaved text. A tab change
that is a `replaceState` is silently undoable by the Android hardware Back button, which would
discard a half-written entry with no warning. Leaving the `writing` tab with a dirty composer must
prompt, using the same `beforeunload` plus SvelteKit `beforeNavigate` guard. This is the only place
in the app where a navigation can destroy user-authored text, so it is worth the special case.

Default values are never written to the URL, so `/clippings` stays clean until something is
actually changed.

#### New dependencies

None.

#### Risks

- Ordering against 3.5: if 3.5 lands first, `offset` has to be retrofitted into the URL. Landing
  3.6 first is cheaper.

---

### 3.7 Preview-then-apply for destructive work

#### Why

`POST /api/vault/rebuild` in [xteink_service/api.py](xteink_service/api.py) iterates every row in
`synced_screenshots` and calls `append_to_daily_note()` for each, with no preview, no diff, and a
bare `except Exception: pass` per note that silently discards failures. It returns only
`{"rebuilt_notes": N}`, so a run that failed on half the vault looks much like one that succeeded.
Given that the vault is Syncthing-synced, a bad rebuild propagates to every peer before anyone
notices.

Alias rescans have the same shape: `_scan_resolve()` in [xteink_service/alias.py](xteink_service/alias.py)
writes titles for every unresolved hash with no chance to inspect the mapping first.

#### Data model

None.

#### API

```
POST /api/vault/rebuild
  body: {"dry_run": true, "book": null}
  dry_run true  -> 200, writes nothing
  dry_run false -> 202 {"job_id": 23}   runs as a 3.3 job

  dry run response:
  {"dry_run": true,
   "total_notes": 184,
   "unchanged": 171,
   "changed": 12,
   "created": 1,
   "failed": 0,
   "changes": [{"path": "Books/Fifteen Dogs.md", "action": "modify",
                "added_lines": 4, "removed_lines": 2,
                "diff": "@@ -18,6 +18,8 @@\n ..."}],
   "errors": []}

POST /api/aliases/rescan
  body: {"dry_run": true}
  dry run response:
  {"dry_run": true,
   "resolved": 7,
   "unresolved": 2,
   "changes": [{"hash": "a1b2...", "current_title": "", "new_title": "Fifteen Dogs",
                "resolved_by": "filename", "action": "set"}]}

POST /api/sync/run
  body: {}
  -> 202 {"job_id": 24}      # triggers a device sync cycle as a 3.3 job
  -> 409 {"detail": "sync already running"}
```

`changes` is capped at 200 entries with a `truncated: true` flag, and each `diff` is capped at 100
lines. A rebuild touching every note must not return a 50 MB JSON body.

`book` scopes a rebuild to one slug, which makes the common case (one note looks wrong) cheap and
low-risk.

**Third consumer: book deletion.** `DELETE /api/books/{slug}` in Plan 2 item 2.7 uses the same
`dry_run` convention and the same `ConfirmApplyDialog.svelte`. Its plan is a per-table count plus a
list of vault paths rather than a text diff, so `ConfirmApplyDialog` must accept either a `changes`
array or a `counts` object and render accordingly. Design the component for both shapes now rather
than retrofitting it later.

#### Service changes

The dry run must produce the exact bytes the real run would write, without writing them. That
requires splitting rendering from writing in
[xteink_service/vault_writer.py](xteink_service/vault_writer.py):

| File | Change |
| --- | --- |
| [xteink_service/vault_writer.py](xteink_service/vault_writer.py) | extract `render_daily_note(book_title, day, entries, kind) -> str` from `append_to_daily_note()`, which then becomes: render, compare to the file on disk, write only if different. This also makes the real run idempotent, which it currently is not in any provable way. |
| [xteink_service/vault_writer.py](xteink_service/vault_writer.py) | new `plan_rebuild(rows, *, book=None) -> RebuildPlan` returning per-path `(action, new_text, old_text)` with no I/O beyond reads |
| [xteink_service/api.py](xteink_service/api.py) | `rebuild_vault()` takes `dry_run`; the apply leg runs under a 3.3 job; **replace the bare `except Exception: pass` with a recorded error entry** so failures are reported instead of swallowed |
| [xteink_service/alias.py](xteink_service/alias.py) | `_scan_resolve(..., dry_run: bool = False)` returning the planned mapping without writing |
| `xteink_service/diffs.py` | new: a thin `unified(old, new, path, max_lines)` wrapper over `difflib.unified_diff` |

The dry run opens no files for writing and creates no directories. That is an assertable property
and the test below asserts it by snapshotting the vault tree's mtimes and sizes before and after.

#### UI

- New `web/src/lib/components/ConfirmApplyDialog.svelte`: takes a plan, renders the counts
  prominently, lists changed paths, and expands a path to show its diff with added and removed lines
  tinted using the Plan 1 item 1.6 semantic tokens. The confirm button is disabled until the plan
  has loaded and reads "Apply 12 changes", never just "OK".
- New `web/src/lib/components/DiffLine.svelte`.
- A "Rebuild notes" control on [web/src/routes/books/[book]/+page.svelte](web/src/routes/books/[book]/+page.svelte)
  scoped to that book, and a global one on [web/src/routes/aliases/+page.svelte](web/src/routes/aliases/+page.svelte)
  next to a "Rescan aliases" control.
- Applying hands off to the `JobWidget` from 3.3 rather than blocking the dialog.

#### New dependencies

None. `difflib` is stdlib.

#### Risks

- **Render extraction changing output.** This is the real risk: if `render_daily_note()` differs by
  even a newline from what `append_to_daily_note()` produced historically, the first dry run reports
  the entire vault as changed. Mitigated by a golden-file test that renders a known row set and
  compares against a committed fixture captured from the current implementation **before** the
  refactor. Capture the fixture first, refactor second.
- **Capture the fixture after Plan 2 item 2.5, not before.** The writing log adds
  `<!-- writing:{id} -->` spans with expanded citations to `Books/<Title>.md`. A fixture captured
  from a note format with no writing spans in it would make the first dry run after 2.5 report every
  note containing an entry as changed, which is exactly the false alarm the fixture exists to
  prevent. If 3.7 ships before 2.5, the fixture has to be recaptured when 2.5 lands.

---

### 3.8 PWA

#### Why

The archive is read on a phone, and today that means a Capacitor WebView pointed at a remote URL
with no offline capability at all: lose the network and the app is blank.
[web/vite.config.ts](web/vite.config.ts) builds with `adapter-static` and `fallback: 'index.html'`,
so the app shell is already a static bundle. A service worker is the missing piece.

#### Data model

None.

#### API

None. The service worker only changes caching of existing responses.

#### Service changes

None.

#### The Capacitor interaction, which is the whole risk

[web/capacitor.config.ts](web/capacitor.config.ts) does not bundle the web assets. It sets
`server.url` to `https://user:pass@xteink.ghostbird.duckdns.org`, so the APK is a WebView pointed at
the live site, with HTTP Basic Auth credentials embedded in the URL because a WebView cannot answer
a Basic Auth challenge on its own.

Consequences that must be designed around:

1. **The service worker registers inside the APK too.** It is the same origin. Android System
   WebView supports service workers, so the APK silently inherits whatever caching strategy ships.
   This is not optional behaviour that can be ignored.
2. **Credentials must never be cached.** The Basic Auth is applied by Nginx Proxy Manager at the
   edge. A cached opaque response could in principle be replayed from the Cache Storage API by
   anything with access to the device's app data. Mitigation: cache **only** same-origin static
   assets and vault images, never `/api/`, never `/logs`, never `/syncs`, and never a response
   carrying `Authorization` or `Set-Cookie`.
3. **Navigation must stay NetworkFirst.** `navigateFallback` would serve the cached shell for a
   request the proxy wanted to answer with a 401 challenge or a redirect, which would make an
   expired or wrong credential look like a broken app. BookOrbit documents this exact decision at
   length in its own Vite config, and the reasoning transfers directly.
4. **APK update behaviour.** With `registerType: 'autoUpdate'`, a WebView holding a stale shell
   updates on next load. Verify this in the APK after enabling, because a service worker that
   pins an old shell inside a WebView the user cannot easily clear is a genuinely bad failure mode.

**Verdict: additive, not conflicting, with one hard requirement.** Ship it, but the manual
verification in the testing plan below is mandatory before the next APK release, and the APK must
be tested with a deliberately wrong credential to confirm the 401 path still surfaces.

#### Configuration

Add `vite-plugin-pwa` to `devDependencies` in [web/package.json](web/package.json) and configure in
[web/vite.config.ts](web/vite.config.ts):

| Setting | Value | Reason |
| --- | --- | --- |
| `registerType` | `autoUpdate` | No "new version available" prompt for a single-user app |
| `workbox.navigateFallback` | **not set** | See point 3 above |
| navigation handler | `NetworkFirst`, `networkTimeoutSeconds: 3`, cache `shell` | Offline still opens, online still honours the proxy |
| `/api/books/*/cover?v=*` and `/api/screenshots/*/image` | `CacheFirst`, 30 days, `maxEntries: 500` | Version-stamped and immutable. This is the whole offline value: the screenshots. |
| other same-origin static assets | `StaleWhileRevalidate` | `/_app/*` is content-hashed already |
| everything under `/api/` not listed above | **no handler**, network only | Never cache data or anything auth-bearing |
| `navigateFallbackDenylist` equivalent | `/api/`, `/logs`, `/syncs`, `/users`, `/health` | Keeps the server-rendered log page and the device endpoints off the worker |
| `manifest` | name, short_name, theme colour from the Plan 1 item 1.6 tokens, shortcuts to `/books`, `/clippings`, `/search` | |

Icons: generate once and commit to `web/static/`. Do not add `@vite-pwa/assets-generator` to the
build, since it would run in the Jenkins Android pipeline for no benefit.

#### New dependencies

`vite-plugin-pwa` as a dev dependency only. It contributes no runtime bundle beyond the generated
service worker and a small registration snippet. It does change [web/package.json](web/package.json),
which means the `npm ci` step in the [Jenkinsfile](Jenkinsfile) pulls it on the next Android build;
confirm the pipeline still passes.

#### Risks

Summarised above. The single highest risk in Plan 3 is a service worker pinning a stale shell inside
the APK. The mitigation is `autoUpdate` plus the mandatory APK verification step.

---

### 3.9 Shareable year poster

#### Why

Pure delight, and it is nearly free once Plan 2 lands: a single canvas render over
`reading_daily_stats` and `reading_attempts`. No backend work beyond one aggregate endpoint.

**Blocked by Plan 2 items 2.1 and 2.2.** The poster must not scan `progress_updates` from the
browser.

#### Data model

None.

#### API

```
GET /api/stats/year-summary?year=2026
  -> 200 {"year": 2026,
          "books_finished": 23,
          "minutes": 14820,
          "pages": 6140,
          "days_read": 212,
          "longest_streak": 41,
          "busiest_month": "2026-03",
          "heatmap": [{"date": "2026-01-01", "minutes": 0}, ...],
          "finished": [{"book_slug": "fifteen-dogs", "title": "Fifteen Dogs",
                        "author": "Andre Alexis", "finished_at": 1762300000,
                        "rating": 5, "cover_url": "/api/books/fifteen-dogs/cover?v=a1b2c3"}]}
```

`heatmap` is one entry per day of the year, read straight from `reading_daily_stats`. `finished`
comes from `reading_attempts` where `outcome = 'finished'`, capped at 60 entries for the poster
grid.

If the year has fewer than Plan 2's `MIN_ACTIVE_DAYS` of data, the response sets
`"low_confidence": true` and the UI says so rather than rendering a misleadingly sparse poster.

#### Service changes

One route in [xteink_service/api.py](xteink_service/api.py) reading the Plan 2 rollup tables. No new
module.

#### UI

- New `web/src/lib/components/SharePosterDialog.svelte`: year picker, live canvas preview, and
  Copy / Download buttons.
- New `web/src/lib/poster.ts`: `renderPoster(summary, opts) -> Promise<Blob>` drawing to an
  `OffscreenCanvas` when available and a detached `<canvas>` otherwise. Layout: title, the four
  headline numbers, a 53 by 7 heatmap grid, and a cover wall of up to 60 covers falling back to the
  procedural placeholder from Plan 1 item 1.5 rendered to canvas.
- Entry point on [web/src/routes/log/+page.svelte](web/src/routes/log/+page.svelte).

Clipboard: `navigator.clipboard.write(new ClipboardItem({'image/png': blob}))`, with a download
fallback. Android WebView image-clipboard support is inconsistent, so the download button is the
primary action on coarse pointers and copy is the primary action on desktop. Feature-detect
`ClipboardItem`; do not assume it exists.

Colours come from the Plan 1 item 1.6 tokens, read at render time with
`getComputedStyle(document.documentElement).getPropertyValue('--heat-4')`, so the poster matches the
active theme instead of hardcoding a palette.

#### New dependencies

None. Canvas 2D only. Do not add a charting or html-to-image library for one render.

#### Risks

- **Font loading.** `canvas.fillText` with a webfont that has not loaded yet silently falls back.
  Await `document.fonts.ready` before rendering.
- **Cover CORS.** All covers are same-origin after 3.2, so the canvas is not tainted and
  `toBlob()` works. This is a concrete reason 3.2 must land before 3.9, beyond aesthetics.

---

## Testing plan

House conventions: pytest with `asyncio_mode = "auto"`, test files under `tests/` named
`test_<module>.py`, Vitest for the frontend with files named `*.test.ts` under `web/src/`. Reminder
from the sibling plans: [conftest.py](conftest.py) pins `STATE_DB` and `KOREADER_DB` to temp files
before import, so no new module may capture a DB path at import time. Every new module below is
written to that rule and the test suite enforces it (see "Cross-cutting" at the end).

### 3.1 Book metadata

New: `tests/test_metadata_registry.py`, `tests/test_metadata_providers.py`, `tests/test_metadata_api.py`.

**All provider tests use mocked HTTP. No test may touch the network.** Patch the aiohttp session
with a fake whose `get()` returns canned payloads; commit the payloads as fixtures captured by hand
from a real response, trimmed.

| Case | Assert |
| --- | --- |
| cache hit | Two `refresh_book()` calls for the same slug issue exactly one HTTP call. Assert on the mock's call count, not on timing. |
| cache miss TTL | A `provider_cache` row older than 30 days triggers a refetch; a 29-day-old row does not. |
| negative cache | A no-results response is cached with `status='miss'` and not re-queried for 24 hours. |
| rate limiting | Two back-to-back calls to the same provider are separated by at least `min_interval_s`. Inject a fake clock; do not `time.sleep` in tests. |
| cooldown on 429 | A 429 sets `cooldown_until`; the next call skips the provider entirely and returns the other provider's result. |
| no Google key | With `GOOGLE_BOOKS_API_KEY` unset, the provider still runs and the URL carries no `key` parameter. |
| `locked_fields` honoured | Lock `title`, refetch with a different title, assert the stored title is unchanged and `skipped_locked == ["title"]`. |
| `PUT` locks | Writing a field adds it to `locked_fields`; `DELETE .../locks/title` removes it. |
| Lucene sanitisation | `clean_query("Hidden Keys, The - Andre Alexis")` contains no `-`. Keep an equivalence test asserting `book_pages._clean_query is registry.clean_query` so the re-export cannot silently diverge. |
| confidence floor | A candidate below `MIN_CONFIDENCE` is rejected and nothing is written. |
| articles excluded | A hash whose `content_kind` is an article is skipped, mirroring the existing `book_pages` rule. |
| host allowlist | A provider configured with an off-allowlist host raises at registry construction, not at call time. |
| no event-loop blocking | `search_books()` contains no `urllib.request` call. Assert by source inspection or by patching `urllib.request.urlopen` to raise and confirming the endpoint still succeeds. |

### 3.2 Covers

New: `tests/test_covers.py`.

| Case | Assert |
| --- | --- |
| allowlist | A URL on `evil.example.com` returns 400 and writes nothing. |
| scheme | `file:///etc/passwd` and `ftp://...` are rejected. |
| size cap | A mocked 5 MB body is rejected and no file is created. |
| content validation | A body that is actually HTML is rejected by the Pillow open check. |
| re-encode | A 3000 px PNG input is stored as JPEG with a long edge of 1000. |
| atomic write | No `cover.jpg.tmp` remains after a successful fetch; a fetch that raises mid-write leaves no `cover.jpg`. |
| version stamp | `cover_version` equals the SHA-256 prefix of the stored bytes and changes when the cover is replaced. |
| cache headers | `GET` with a matching `v` returns `immutable`; a stale or absent `v` returns `no-cache`. |
| idempotence | A second fetch with `force=false` performs no HTTP call. |
| rebuild safety | Running `rebuild_vault()` leaves `cover.jpg` byte-identical. |

### 3.3 Long-job progress

New: `tests/test_jobs.py`, `tests/test_jobs_stream.py`.

| Case | Assert |
| --- | --- |
| lifecycle | `with job('ocr', 'x', total=3)` leaves one row `state='done'`, `processed=3`, `ended_at` set. |
| failure | An exception inside the block sets `state='failed'`, records a sanitised `error`, and **re-raises**. |
| transparency | A job wrapper around a function returns that function's exact return value. |
| write coalescing | 100 `advance()` calls inside 0.1 s produce at most 2 DB writes. Inject a fake clock. |
| monotonic rev | Every insert and update strictly increases `rev` across concurrent jobs. |
| orphan sweep | A `running` row with a foreign pid becomes `stale` on `init_db()`. |
| retention | A `done` row older than 7 days is deleted on `init_db()`; a 6-day-old row survives. |
| SSE shape | The stream emits `event: job` with an `id:` equal to `rev` and a JSON `data:` line, terminated by a blank line. Parse the raw bytes, do not trust a helper. |
| SSE cursor | `?cursor=N` emits only rows with `rev > N`. |
| SSE resume | A `Last-Event-ID` header is honoured when `cursor` is absent. |
| SSE multi-client | Two concurrent streams both receive the same update. |
| SSE cap | Stream 9 with `MAX_STREAMS=8` returns 503, and the counter decrements when a stream closes (assert via a third connection succeeding after a close). |
| headers | `Cache-Control: no-cache` and `X-Accel-Buffering: no` are present. |

Use `httpx.ASGITransport` against the FastAPI app and read the stream with a timeout, which is how
the existing `tests/test_koreader_sync.py` already drives the app.

### 3.4 Command palette

Frontend only. New: `web/src/lib/palette.test.ts`, `web/src/lib/components/CommandPalette.test.ts`.

| Case | Assert |
| --- | --- |
| debounce | Three keystrokes within 150 ms issue one fetch. |
| abort | A second keystroke aborts the first request (`AbortController.abort` called). |
| bounds | With 50 results of one kind, the `all` tab renders 5. |
| tabs | `Tab` cycles and refilters without a new fetch when results are already grouped. |
| keyboard | Arrow keys move the active index and wrap; `Enter` triggers navigation; `Esc` closes. |
| actions | An empty query renders the action list; typing "theme" matches the toggle-theme action. |
| single instance | Opening from two triggers yields one dialog in the DOM. |

### 3.5 Windowing and jump rail

New: `tests/test_pagination.py`; `web/src/lib/window.test.ts`,
`web/src/lib/components/WindowedList.test.ts`.

| Case | Assert |
| --- | --- |
| offset paging | Pages of 10 over 25 rows return 10, 10, 5 with no duplicates and no gaps. |
| total | `total` matches the unpaginated count and excludes tombstoned and soft-deleted rows. |
| bucket offsets | Each bucket's `offset` equals the running sum of preceding counts, and `sum(count) == total`. |
| bucket keys | `by=month` keys are `YYYY-MM` and sort descending. |
| device contract untouched | A regression test asserting `GET /api/v1/clippings/{doc}` still satisfies the strict firmware pagination rules. This is the most important case in the item. |
| `ensureRange` dedupe | Overlapping range requests issue one fetch per page. |
| sparse render | A window with pages 0 and 3 loaded renders real rows at those offsets and skeletons between. |

### 3.6 URL state

Frontend only. New: `web/src/lib/urlstate.test.ts`.

| Case | Assert |
| --- | --- |
| round trip | Setting a param updates the URL; reloading restores the value. |
| defaults omitted | A default value writes no query string. |
| replace vs push | A filter change calls `goto` with `replaceState: true`; a record navigation does not. |
| history hygiene | Ten keystrokes in the search box add at most one history entry. |

### 3.7 Preview-then-apply

New: `tests/test_rebuild_preview.py`, `tests/test_vault_render.py`.

| Case | Assert |
| --- | --- |
| **dry run writes nothing** | Snapshot every path, size, and mtime under the temp vault before and after a dry run and assert exact equality. Also patch `builtins.open` to raise on any write mode and confirm the dry run still succeeds. |
| golden render | `render_daily_note()` over a fixed row set matches a committed fixture. **Capture this fixture from the current implementation before refactoring.** |
| idempotence | Rebuilding twice reports `changed: 0` on the second run. |
| diff correctness | A note with one added line reports `added_lines: 1`, `removed_lines: 0`. |
| caps | 500 changed notes return 200 entries and `truncated: true`. |
| errors surfaced | A row that raises during render appears in `errors` rather than being silently skipped. This is the current bug; assert the fix. |
| scoping | `book=<slug>` touches only that book's note. |
| alias dry run | `POST /api/aliases/rescan` with `dry_run` leaves `document_aliases` unchanged. |
| sync lock | A second `POST /api/sync/run` while one is active returns 409. |

### 3.8 PWA

Automated coverage is limited; most of the value is manual.

| Case | How |
| --- | --- |
| build succeeds | `npm run build` produces `build/sw.js` and a manifest. |
| no API caching | Assert the generated `sw.js` contains no runtime caching rule matching `/api/` other than the two explicit image routes. A string assertion over the generated file is crude but catches the regression that matters. |
| denylist | Assert `/logs` and `/syncs` are excluded. |

Manual, and **mandatory before the next APK release**:

1. Build and install the APK. Confirm the app still loads with valid credentials.
2. Put the device in airplane mode. Confirm the shell opens and previously viewed screenshots render
   from cache, and that `/api/` views show a clear error rather than stale data.
3. Deploy a visible UI change, reopen the APK twice, and confirm the change appears. This is the
   stale-shell check.
4. Temporarily set a wrong `APP_BASIC_AUTH`, rebuild, and confirm the 401 surfaces instead of a
   cached shell.
5. Confirm the Jenkins Android pipeline still passes with the new dev dependency.

### 3.9 Share poster

New: `tests/test_year_summary.py`; `web/src/lib/poster.test.ts`.

| Case | Assert |
| --- | --- |
| day coverage | `heatmap` has 365 entries, 366 in a leap year, including zero-minute days. |
| boundaries | A session spanning midnight on 31 December is attributed by the rollup's rule, not double-counted. |
| low confidence | Below `MIN_ACTIVE_DAYS`, `low_confidence` is true. |
| caps | `finished` is capped at 60. |
| no event-log scan | The endpoint issues no query against `progress_updates`. Patch `_koreader_conn` to raise and confirm the endpoint still succeeds. |
| poster render | `renderPoster()` returns a non-empty `image/png` blob in jsdom with a stubbed canvas. |
| clipboard fallback | With `ClipboardItem` undefined, the download path is used and no exception escapes. |

### Cross-cutting

Add `tests/test_module_hygiene.py` asserting that no module under `xteink_service/` other than
`koreader_sync` reads `STATE_DB` or `KOREADER_DB` at import time. Implement by importing each module
with the environment variables set to a sentinel path and confirming no module-level attribute holds
it. This mechanises the rule all three plans state and that is otherwise enforced only by review.

### Existing tests most likely to break

| File | Why | Action |
| --- | --- | --- |
| [tests/test_api.py](tests/test_api.py) | `/api/clippings` and `/api/highlights` change from a bare array to an envelope (3.5). `/api/books` gains `cover_url` (3.2). `rebuild_vault` changes shape (3.7). Its hand-built fixture DB also lacks the new tables, so every new endpoint must tolerate a missing table rather than 500. | Update assertions; confirm `init_db()` is called at the top of each new endpoint. |
| [tests/test_clippings.py](tests/test_clippings.py) | Shares the state DB and has an autouse fixture that deletes from `clippings`. New indexes are harmless, but the device-contract regression test in 3.5 belongs here. | Add the firmware pagination regression test. |
| [tests/test_vault_writer.py](tests/test_vault_writer.py) and [tests/test_vault_integration.py](tests/test_vault_integration.py) | The 3.7 render extraction is the highest-risk refactor in this plan. | Capture the golden fixture before refactoring; expect these to be the first to fail if output drifts. |
| [tests/test_book_pages.py](tests/test_book_pages.py) | `_clean_query` moves and is re-exported; `_store()` gains a `book_metadata` write. | Keep the existing cases, add the re-export identity assertion. |
| [tests/test_archiver.py](tests/test_archiver.py) | `run_sync()` is wrapped in a job (3.3). | Assert the return value is unchanged and that a missing `jobs` table does not break archiving. |
| [tests/test_koreader_sync.py](tests/test_koreader_sync.py) | Runs against an effectively empty `state.db`. New endpoints must not be reachable on `PUBLIC_SYNC_HOST`. | Add an explicit test that `/api/jobs`, `/api/jobs/stream`, and `/api/books/{slug}/cover` are refused on the public sync host. |
| [tests/test_logbuffer.py](tests/test_logbuffer.py) | Unaffected, but the jobs feed deliberately mirrors its cursor semantics. | Reference it when writing `test_jobs_stream.py`. |

---

## Acceptance criteria

**3.1 Book metadata**
- [ ] `book_metadata`, `provider_cache`, and `provider_state` exist and are created idempotently.
- [ ] Two providers are registered, each with a single allowlisted host and a minimum call interval.
- [ ] A refetch never overwrites a field listed in `locked_fields`, and the response reports what it skipped.
- [ ] Editing a field through `PUT` locks it; the lock is visible and removable in the UI.
- [ ] A missing `GOOGLE_BOOKS_API_KEY` degrades to Open Library with no error shown to the user.
- [ ] `GET /api/tbr/search` no longer performs a blocking `urlopen` on the event loop.
- [ ] `_clean_query` has exactly one definition in the codebase.
- [ ] No test contacts the network.

**3.2 Covers**
- [ ] Covers are stored at `VAULT_PATH/Books/<Title>/cover.jpg` and recorded in `books.cover_path` plus `books.cover_version`.
- [ ] Only allowlisted hosts over http or https, maximum 2 MB, validated by Pillow, re-encoded to JPEG at a 1000 px long edge.
- [ ] Writes are atomic via a temp file plus `os.replace`.
- [ ] `GET /api/books/{slug}/cover?v=` returns `immutable` for a matching version.
- [ ] `/api/books` returns a relative `cover_url`; no page hotlinks an external host.
- [ ] `BookCover.svelte` falls back to the Plan 1 placeholder on both absence and load error.
- [ ] A vault rebuild leaves covers byte-identical.

**3.3 Long-job progress**
- [ ] `jobs` exists with a monotonic `rev`, and orphaned `running` rows become `stale` on startup.
- [ ] The `job()` context manager never changes the return value or the exception behaviour of the work it wraps.
- [ ] Progress writes are coalesced to at most one every 0.5 s.
- [ ] Sync, OCR, page lookup, alias scan, vault rebuild, and metadata refresh all report through it.
- [ ] `GET /api/jobs/stream` is a valid SSE stream with `id:` equal to `rev`, honours `Last-Event-ID`, survives a server restart via reconnect, and caps concurrent streams with a documented client fallback.
- [ ] `JobWidget.svelte` is mounted once and hidden when idle.

**3.4 Command palette**
- [ ] Opens with `Cmd/Ctrl+K` and from an on-screen control usable on mobile.
- [ ] Debounced at 150 ms with in-flight requests aborted.
- [ ] Tabbed, bounded per section, and includes runnable actions.
- [ ] Uses the Plan 1 search endpoint and adds no separate ranking.
- [ ] One shared instance across all triggers.

**3.5 Windowing and jump rail**
- [ ] `/clippings` and `/highlights` have no hard result cap and report an accurate `total`.
- [ ] Bucket offsets sum correctly and drive the list endpoint with no client arithmetic.
- [ ] The device-facing `/api/v1/clippings` pagination contract is unchanged and regression-tested.
- [ ] Overlapping range requests fetch each page once.

**3.6 URL state**
- [ ] Filters, offsets, and tabs survive a reload and are linkable.
- [ ] Filter changes use `replaceState`; Android hardware Back still exits the app from the root.
- [ ] Default values are not written to the URL.

**3.7 Preview-then-apply**
- [ ] A dry run provably writes nothing, asserted by both a filesystem snapshot and a patched `open`.
- [ ] Rendering is separated from writing, and `append_to_daily_note()` is idempotent.
- [ ] A golden fixture captured before the refactor still matches after it.
- [ ] The bare `except Exception: pass` in `rebuild_vault()` is gone and failures are reported.
- [ ] Rebuild can be scoped to one book.
- [ ] Alias rescan has the same dry run.
- [ ] The apply leg runs as a 3.3 job.

**3.8 PWA**
- [ ] The service worker caches static assets and version-stamped images only; nothing under `/api/` except the two explicit image routes.
- [ ] Navigation is NetworkFirst with no `navigateFallback`.
- [ ] `/logs`, `/syncs`, `/users`, and `/health` are excluded.
- [ ] The APK loads, works offline for cached screenshots, picks up a deployed change within two launches, and still surfaces a 401 on a wrong credential.
- [ ] The Jenkins Android pipeline passes.

**3.9 Share poster**
- [ ] `GET /api/stats/year-summary` reads only the Plan 2 rollup tables.
- [ ] The heatmap covers every day of the year including zeroes, and flags low confidence.
- [ ] The poster renders to a PNG blob, copies where supported, and always offers a download.
- [ ] Colours are read from the active theme tokens at render time.

---

## Out of scope

### Deferred from the original Phase 4

These were considered during the BookOrbit audit and dropped. Each is a new external dependency and
a new failure mode, and none of them improves the core loop of capturing and rereading passages.

- **Readwise push.** One-way export of clippings and highlights. Deferred: it duplicates the export
  in Plan 1 item 1.2 for an audience of one.
- **Hardcover progress push.** Bidirectional GraphQL sync. Deferred: it needs an identity mapping
  from `book_slug` to Hardcover's book ID, which is the same matching problem `document_aliases`
  already solves imperfectly.
- **StoryGraph progress push.** Cookie-authenticated and therefore fragile. Deferred for the same
  identity-mapping reason, plus the cookie will expire silently.
- **OPDS feed.** A pull-based alternative to the planned `/essays` push over Calibre Wireless.
  Deferred until the Calibre Wireless `PUT /upload` investigation in [TODO.md](TODO.md) concludes,
  since it is only worth building if that path fails and the firmware turns out to speak OPDS.
- **Passage-level semantic search.** Embedding every clipping and OCR block with `sqlite-vec` to
  find similar passages. The most genuinely novel idea in the audit and the most work. Revisit only
  after Plans 1 and 2 are in daily use.

### Also out of scope for Plan 3

- Any multi-user concept: accounts, permissions, sharing. The perimeter auth model stands.
- A general job queue with retries, leases, and scheduling. `jobs` is a progress surface, not a
  queue. If background work ever needs retry semantics, that is a separate design.
- An audit log. `jobs` and `logbuffer` are both deliberately ephemeral.
- Full list virtualisation. Item 3.5 explains why sparse windowing is the right size here.
- A charting library. Plan 2 item 2.4 settles the charting question; 3.9 draws to canvas directly.
- Cover art editing, cropping, or search across multiple sources. One automatic fetch plus a manual
  URL override is enough.
- Replacing `book_pages` with `book_metadata`. They coexist, for the reasons given in 3.1.
