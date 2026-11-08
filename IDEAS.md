# Ideas from BookOrbit

A plan document. Source: a feature audit of `bookorbit/` (NestJS + Vue, 90 modules, 163 tables,
multi-user, tens-of-thousands-of-books scale) measured against this project's current state
(FastAPI + SvelteKit, SQLite, single user, ~15 tables, one device family).

**Status: phases 1 to 3 are committed and have detailed implementation plans. Phase 4 is dropped.**

| Phase | Implementation plan |
| --- | --- |
| 1. Cheap wins | [PLAN-1-QUICK-WINS.md](PLAN-1-QUICK-WINS.md) |
| 2. The reading-data model | [PLAN-2-READING-DATA.md](PLAN-2-READING-DATA.md) |
| 3. Polish and navigation | [PLAN-3-POLISH.md](PLAN-3-POLISH.md) |
| 4. Outward integrations | dropped, see [PLAN-3-POLISH.md](PLAN-3-POLISH.md) "Out of scope" |

Those three documents carry the exact SQL, API shapes, file-level changes, testing plans, and
acceptance criteria. This document stays as the rationale: what was found in BookOrbit, what is
worth taking, and what is deliberately left behind.

Note that the "Plan 1 / 2 / 3" numbering in those documents is a forward-looking split of this
audit. It is not the historical build phases 1 to 11 recorded in [TESTING.md](TESTING.md), which are
all complete.

The goal is not to become BookOrbit. This tool owns something BookOrbit does not: **a screenshot +
OCR commonplace archive that writes into an Obsidian vault**. BookOrbit owns the *reading-data*
model, and that is where almost all the transferable value sits.

---

## 0. Framing

| | This tool | BookOrbit |
|---|---|---|
| Scope | One reader's commonplace book | Multi-user library server |
| Unit of value | A captured passage | A catalogued book |
| Source of truth | SQLite + the vault markdown | PostgreSQL |
| Books | Inferred from screenshots + kosync hashes | First-class, scanned from disk |
| Reading data | `progress_updates` rows | sessions, attempts, daily stats, annotations, journal |
| Metadata | Open Library page counts only | 12 providers + cache + scoring + locks |
| Covers | None | Store, slots, provenance, procedural placeholders |

Three things follow:

1. **Do not port library management.** No libraries, collections, smart scopes, entity manager,
   book requests, indexers, download clients, migration adapters, read-aloud, TTS, Kobo store
   proxy, permissions, or the spreadsheet table view. All of it is priced for a scale and a user
   model this tool does not have.
2. **Do port the reading-data model.** `reading_sessions`, `reading_attempts`, `user_book_status`,
   `annotations` soft-delete, `book_journal_entries`, `user_reading_daily_stats`. These are the
   parts of BookOrbit that would make this tool materially better at being a commonplace book.
3. **Port the cheap UI wins.** Export menus, trash, "on this day", FTS, placeholder covers,
   theme tokens. Each is days, not weeks, and each is visible on every page.

---

## Phase 1: cheap wins (highest value per hour)

### 1.1 Replace `LIKE '%q%'` search with SQLite FTS5

Today `GET /api/search` ([xteink_service/api.py](xteink_service/api.py)) does five `LIKE '%q%'`
scans across `book_title`, `ocr_text`, `ocr_corrected`, `user_notes`, `highlights.selected_text`.
There is one index in the entire schema (`idx_clippings_doc_rev`). OCR text is the bulk of the
data and it is exactly what full-text search is for.

BookOrbit's answer is `pg_trgm` + a custom unaccent + relevance weighting per field
(`server/src/modules/search/search.service.ts`). The SQLite equivalent is free:

- Add an FTS5 virtual table `search_index(kind, ref_id, book_slug, title, body)` with external
  content or triggers from `synced_screenshots`, `highlights`, `clippings`, and notes.
- Rank with `bm25(search_index, <per-column weights>)` so a title hit outranks an OCR hit.
- Return `snippet()` with the match highlighted instead of the client guessing.
- Keep the existing `notes_only` filter as a `kind` facet, and add facets for
  screenshot / highlight / clipping / note with counts (BookOrbit's `FormatFacetChips` pattern).

This also unblocks the command palette (3.4) and makes the archive usable once it is years deep.

### 1.2 Export highlights and clippings

BookOrbit has `annotation-export.service.ts` (Markdown, CSV, JSON) plus a small popover
`HighlightsExportMenu.vue`, grouped by chapter with quote blocks and notes.

This tool already generates vault markdown, but there is no way to pull a single book's passages
out of the web UI. Add:

- `GET /api/books/{slug}/export?format=md|json|csv&include=highlights,clippings,notes`
- A popover on `/books/[book]` and on `/clippings` / `/highlights`.
- Group by chapter when `clippings.chapter` is populated, else by date.

Small, self-contained, and it makes the web UI a second export path alongside the vault.

### 1.3 Soft delete + trash + restore

`DELETE /api/clippings/{id}` already tombstones (correctly, because hard deletes resurrect from
the device). `DELETE /api/highlights/{id}` hard deletes and leaves the `==text==` marks in the
Obsidian note behind.

BookOrbit uses `deletedAt` + a restore endpoint + a purge confirmation dialog, and a
`notebook/trash` view. Port all three:

- Add `deleted_at` to `highlights`; stop hard deleting.
- `POST /api/highlights/{id}/restore`, `POST /api/clippings/{id}/restore`.
- A `/trash` page listing both, with "restore" and an explicit "purge permanently" confirm.
- While here: make highlight deletion also strip the `==…==` marks from the note, which is
  currently a known gap.

### 1.4 "On this day" and a review queue

BookOrbit's `notebook` module has `on this day` and a `review queue`; the dashboard has
`HighlightOfTheDay`.

This is the single most commonplace-book-shaped feature in the whole of BookOrbit, and this tool
has the data for it already (`synced_screenshots.sync_date`, `highlights.created_at`,
`clippings.created_at`).

- `GET /api/on-this-day?date=` returning passages captured on this calendar day in prior years.
- `GET /api/review?strategy=spaced|random&limit=` for a resurfacing queue.
- Surface "on this day" on the home page, and a `/review` page that shows one passage at a time.

### 1.5 Procedural placeholder covers

There is no cover art anywhere in this tool. BookOrbit generates nine procedural styles from a
deterministic hash (`client/src/features/book/lib/placeholder/`), which gives every book a
distinct, stable spine with no metadata provider involved.

Port **one** style (`clothbound` or `bauhaus`) as a Svelte component that takes the canonical book
slug, hashes it to a palette, and renders SVG. No backend work, no network, instant visual
upgrade to `/books`, the sidebar index, and search results.

### 1.6 OKLCH theme tokens

BookOrbit's `client/src/assets/theme/tokens.css` drives every neutral surface from a single
`--tint-h` / `--tint-c` pair, and dark mode only shifts lightness and chroma. Semantic ramps
(`--score-*`, `--volume-*`) rather than raw colours.

`web/src/styles/app.css` currently has an ad-hoc palette. Restructuring it into the same shape is
about 100 lines of CSS and makes dark mode, accent choice, and the reading heatmap palette all
fall out for free.

---

## Phase 2: the reading-data model (the real payoff)

Today a "reading session" does not exist as a concept. `progress_updates` is an append-only log of
kosync pushes, and `GET /api/reading-stats` loads **every row** into memory to compute aggregates.
That is the bottleneck behind every statistic the app could show.

### 2.1 Derive reading sessions

BookOrbit models `reading_sessions` explicitly (client-UUID idempotent, `source` enum, server-
computed duration, progress delta) plus `reading_session_sync_cursors` for devices that only report
counters.

This tool cannot get sessions from the device, but it can **derive** them: group consecutive
`progress_updates` for one document by a gap threshold (say 30 minutes), and materialise:

```
reading_sessions(
  id, document, book_slug, device_id,
  started_at, ended_at, duration_s,
  start_pct, end_pct, pct_delta,
  pages_delta,          -- via book_pages
  derived_from_updates  -- count, for auditing
)
```

Backfill once from the existing log, then extend incrementally as updates arrive in
[xteink_service/koreader_sync.py](xteink_service/koreader_sync.py). Idempotent: re-deriving a day
must produce the same rows.

### 2.2 Daily rollups

BookOrbit has `user_reading_daily_stats` precisely so the stats pages do not scan the event log.
Add `reading_daily_stats(date, minutes, pct_sum, pages, books_touched, sessions)` maintained on
write. Every endpoint in 2.4 then reads one small table.

This also fixes the full-table scans currently in `/api/reading-stats`, `/api/books`,
`_linked_documents`, and `_book_titles_for_slug`.

### 2.3 Read status, reading attempts, and ratings

The current model collapses a book to "has progress" / "finished". BookOrbit has:

- `user_book_status` with 8 states (`unread`, `want_to_read`, `reading`, `on_hold`, `rereading`,
  `read`, `skimmed`, `abandoned`) and an `auto` / `manual` source so a device-derived status never
  silently overwrites a human one.
- `reading_attempts` as first-class re-read records with start/end dates, outcome, and a per-attempt
  1-5 rating.
- `user_book_ratings` and `user_book_notes` separate from the above.

This matters here because the percentage resetting to 0 after a finish is currently indistinguishable
from a new book. Port:

- A `books` table keyed on `canonical_book_slug` (title, author, pages, status, status_source,
  first_seen, cover_path). It unifies today's three implicit book sources: screenshots,
  `progress_updates`, and `tbr_books`.
- `reading_attempts(book_slug, started_at, finished_at, outcome, rating, notes)`.
- **Fold `tbr_books` into it**: "to be read" becomes `status = want_to_read`, which removes the
  current split where a TBR entry and the book you then read are unrelated rows.

### 2.4 A real statistics page

Once 2.1-2.3 exist, BookOrbit's ~25 user-statistics routes become cheap. Pick the ones that make
sense for one reader:

- GitHub-style reading heatmap with fixed bins and a `MIN_ACTIVE_DAYS` low-confidence guard
  (`ReadingHeatmapChart.vue`, `lib/heatmap-palette.ts`).
- Reading streak, pace (pages/hour), peak hours, favourite days.
- Completion timeline and a year projection against a goal.
- Per-book progress trace (BookOrbit's `ReadingLogTraceChart.vue`) which the existing
  `/api/books/{slug}/reading-calendar` data already nearly supports.

Use a chart library that reads CSS theme tokens at runtime so charts recolour with the theme
(`useChartTokens.ts`).

### 2.5 Writing log

BookOrbit's `book_journal_entries` are dated freeform entries with an optional quote, chapter, and
position. This tool only has `user_notes` attached to a single screenshot, which is the wrong
granularity for a thought about the book as a whole.

BookOrbit is the starting point here, not the destination. Its journal entry holds one quote in a
text column. A commonplace book needs writing that **cites** the archive: long-form entries about a
book that embed specific clippings, highlights, and screenshots inline as evidence. That is the
actual point of the whole tool, and it is the one feature in this plan that BookOrbit does not
have a real answer for, because BookOrbit's highlights live next to the book rather than inside the
reader's own prose.

So: `writing_entries` for the prose plus `writing_refs` for the citations, where each citation
stores a `snapshot` of the quoted text taken at citation time. The snapshot is what makes the
writing durable when the underlying clipping is later deleted or re-synced under a different hash.
Rendered on `/books/[book]`, interleaved with screenshots and progress, and written into the vault
note under the existing `## YYYY-MM-DD` headings with the citations expanded, so the Obsidian copy
reads as finished prose rather than a list of tokens.

Full design in [PLAN-2-READING-DATA.md](PLAN-2-READING-DATA.md) item 2.5.

### 2.6 Persist the screenshot ↔ clipping link

[xteink_service/clip_match.py](xteink_service/clip_match.py) already computes which screenshot shows
a given clipping, but nothing is persisted and the match is recomputed per request. BookOrbit's
`annotation_positions` table stores resolved anchors with a status
(`exact | repaired | failed | pending`) and a converter version so a later algorithm change can
re-run only the failures.

Add `clipping_screenshot_links(clipping_id, screenshot_id, status, matcher_version, matched_at)`.
Cheap, removes per-request work, and makes "show me the page this quote came from" reliable.

---

## Phase 3: polish and navigation

### 3.1 Book metadata beyond page counts

[xteink_service/book_pages.py](xteink_service/book_pages.py) queries Open Library for
`number_of_pages_median` and nothing else. BookOrbit has a provider registry with per-provider
throttling, a cache table, candidate relevance scoring, and per-field locks.

Scaled down to one user, that is:

- `book_metadata(slug, title, author, published_year, isbn13, description, cover_url, page_count,
  source, fetched_at, locked_fields)`.
- A tiny registry with two adapters (Open Library, Google Books) behind one interface, a shared
  cache table, and a minimum interval between calls per provider.
- `locked_fields` so a hand-corrected title is never overwritten by a later fetch. This is the one
  piece of BookOrbit's metadata machinery that is worth having even at n=1.

### 3.2 Covers

With 3.1 in place, download the cover once into the vault (`Books/<Title>/cover.jpg`) and serve it
with a version-stamped URL (`?v=`) so a refresh repaints. Fall back to the procedural placeholder
from 1.5. BookOrbit's `CoverFill.vue` blurred-fit letterboxing is a nice touch for mismatched
aspect ratios.

### 3.3 Long-job progress

File Transfer sync, OCR batches, page-count lookups, and vault rebuild are all invisible except in
`/logs`. BookOrbit models in-flight work as `{ label, processed, total, failed }` and ships a
persistent floating widget plus a websocket feed, with a **toast-claim** so a page rendering its own
progress suppresses the global toast (`useScanProgress.ts`).

Here, SSE is enough (no new dependency, FastAPI supports it natively):

- `jobs(id, kind, label, processed, total, failed, state, started_at, ended_at)`.
- `GET /api/jobs/stream` SSE, `GET /api/jobs` for the current set.
- A floating `JobWidget.svelte` mounted once in `+layout.svelte`.

### 3.4 Command palette

For a personal archive the palette *is* the navigation. BookOrbit's version
(`useCommandPaletteResults.ts`, `lib/rank.ts`) is tabbed, bounded per section, debounced, ranked,
and includes runnable actions, not just records.

Backed by FTS from 1.1: books, highlights, clippings, screenshots by OCR text, plus actions
(trigger sync, rebuild vault, toggle theme, open logs).

### 3.5 Windowed lists and a jump rail

`/clippings` caps at 500 and `/highlights` at 200 because the frontend renders everything. BookOrbit
solves this with sparse windowing (`BookSlot` placeholders + `ensureRange(start, end)`) rather than
full virtualisation, plus a server-computed **jump rail** (A-Z or date scrubber).

A date scrubber is the right one here: an archive is date-ordered, and jumping to "March 2026"
beats scrolling. `GET /api/clippings/buckets` returns `[{label, offset, count}]`; the client stays
dumb.

### 3.6 URL-synced view state

BookOrbit round-trips filter and sort state through the URL (`useAnnotationsUrlSync.ts`) so every
view is linkable and reload-safe. Cheap in SvelteKit with `$page.url` + `goto(..., { replaceState })`.
Apply to `/search`, `/clippings`, `/highlights`, and the book detail tabs.

### 3.7 Preview-then-apply for destructive work

`POST /api/vault/rebuild` currently rewrites every note with no preview. BookOrbit's bulk-rename
flow previews a diff (`PathDiffLine.vue`) before committing. Same treatment for vault rebuild,
alias rescans, and any future OCR re-run.

### 3.8 PWA

Capacitor already wraps the UI as an Android app, but a PWA is complementary and nearly free with
`vite-plugin-pwa`. The transferable trick from `client/vite.config.ts`: `CacheFirst` for
version-stamped images (`?v=`), `StaleWhileRevalidate` otherwise, and `NetworkFirst` for navigation
so the auth proxy can still redirect. That makes the vault PNGs readable offline on a phone.

### 3.9 Shareable poster

`ShareImageDialog.vue` + `lib/share-poster.ts` render a "year in reading" poster to canvas with
copy-to-clipboard. Zero backend work once Phase 2 lands, and high delight.

---

## Phase 4: dropped

Four outward integrations were considered and are not being built: a Readwise push, a
Hardcover or StoryGraph progress push, an OPDS feed as an alternative to the planned `/essays`
push, and passage-level semantic search with `sqlite-vec`.

Each is a new external dependency and a new failure mode, and the two tracker pushes need an
identity mapping from `canonical_book_slug` to an external book ID, which is the same matching
problem `document_aliases` already solves imperfectly. The reasoning for each is recorded in the
"Out of scope" section of [PLAN-3-POLISH.md](PLAN-3-POLISH.md) so it is not lost. Semantic search is
the one worth revisiting once phases 1 and 2 are in daily use.

---

## Explicitly not porting

| BookOrbit feature | Why not |
|---|---|
| Multi-user, permissions, OIDC, audit log | Single user behind a reverse proxy. The perimeter model is correct. |
| Libraries, collections, smart scopes | There is no library here; books are inferred from reading activity. |
| Saved views / column presets / three-tier persistence | Priced for a browse UI over tens of thousands of books. |
| Spreadsheet table with inline cell editing | Same. |
| Bulk metadata edit, entity manager, duplicate detection | Same. |
| Book requests, indexers, download clients | Acquisition is not this tool's job. |
| Read-aloud worker, TTS | Separate container, GPU probing, a whole pipeline. No. |
| Kobo store proxy, KEPUB conversion | Wrong device family. |
| Migration adapters | Nothing to migrate from. |
| Multi-format readers (EPUB/PDF/CBZ/DjVu/audio) | Reading happens on the X4. The web UI is an archive, not a reader. |
| Achievements and gamification | Tempting, but it is a large surface for a private notebook. |

---

## Suggested order

1. **1.1 FTS5** - unblocks 3.4, fixes the worst current scan.
2. **1.3 soft delete + trash** - fixes a known bug (orphaned `==` marks) on the way.
3. **1.5 placeholder covers + 1.6 theme tokens** - a day each, visible everywhere.
4. **1.4 on this day / review** - the feature most true to the tool's purpose.
5. **1.2 export** - small, and it closes the loop with the vault.
6. **2.1 + 2.2 sessions and rollups** - the foundation; also removes the full-table scans.
7. **2.3 books table and attempts** - absorbs `tbr_books`, fixes the re-read ambiguity.
8. **2.4 statistics** - now cheap.
9. Everything else as appetite allows.

Phase 1 is independent and can land in any order. Phase 2 should land in sequence: 2.1 and 2.2
before 2.4, and 2.3 before anything that wants a stable book identity (3.1, 3.2).

Phase 3 reorders slightly once planned in detail: 3.3 (job progress) is worth building first
because 3.1, 3.2 and 3.7 all report through it. See the ordering table in
[PLAN-3-POLISH.md](PLAN-3-POLISH.md).

---

## Notes on existing docs

While auditing, three staleness issues turned up that are worth fixing regardless of this plan:

- `TODO.md` still lists the Android app and Jenkins CI as unchecked, but `web/capacitor.config.ts`,
  `web/android/`, `Jenkinsfile`, and `Dockerfile.jenkins` all exist.
- `ARCHITECTURE.md` still describes a WebSocket "Status Display" on the X4 screen in its overview
  and flow diagram, contradicting its own section 3 and `AGENT.md`. Port 81 is a file-upload
  channel, not a status display.
- `PROJECT_SPEC.md` assumption 3 says CrossPoint has no "Send Document Metadata" option; it does
  now, and the code supports it.
