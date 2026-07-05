# Web UI Plan

## Layout (barnsworthburning-inspired)

```
┌──────────────┬───────────────────────────────┬──────────────────────┐
│  Sidebar     │  Main column                  │  Detail panel        │
│  (fixed)     │  (scrollable)                 │  (slides in)         │
│              │                               │                      │
│  Nav links   │  [active view]                │  Screenshot detail   │
│  ─────────── │                               │  full image          │
│  Book index  │                               │  OCR text            │
│  with counts │                               │  edit fields         │
│              │                               │  reading progress    │
└──────────────┴───────────────────────────────┴──────────────────────┘
```

---

## Navigation (sidebar)

| Element | Route | Content |
|---------|-------|---------|
| **Books** | `/books` | Masonry gallery of all books |
| **Reading Log** | `/log` | Chronological KOReader progress |
| **TBR** | `/tbr` | To-be-read list |
| **Essays** | `/essays` | Essay-of-the-day download + push to device |
| **Aliases** | `/aliases` | Hash → title mapping table |
| **Status** | (sidebar widget) | Last sync time, today's count |

Book index below nav links: alphabetical list of all book titles with screenshot count. Clicking selects that book and loads its gallery.

---

## Views

### `/books` — Book Gallery

- **Main column**: masonry grid of book cards
- Each **BookCard** shows:
  - Book title
  - Screenshot count
  - Date of last sync
  - Thumbnail of most recent screenshot
- Clicking a BookCard navigates to `/books/[book]`

### `/books/[book]` — Book Detail

- **Main column**: masonry grid of ScreenshotCards for that book
  - Sorted by date (newest first within each `## YYYY-MM-DD` group)
  - Date headings separate groups
- **Left sidebar** highlights the active book
- Clicking a ScreenshotCard opens the **Detail Panel** (no navigation)

### `/log` — Reading Log

- **Main column**: timeline of KOReader progress entries
  - Grouped by date, newest first
  - Each entry: book title, percentage, section marker (§N), timestamp
  - Resolved title shown; unresolved hash shown in muted style with "Map →" link
- Clicking a book title navigates to `/books/[book]`

### `/aliases` — Hash → Title Map

- **Main column**: table of all `document_aliases`
  - Columns: hash (truncated), filename, title, resolved_by
  - Inline edit: click title cell → text input → save with PUT `/api/aliases/{hash}`
  - "Unresolved" filter: show only hashes with `title = ?` from reading log
- No detail panel for this view

---

## Detail Panel (trail — slides in from right)

Triggered by clicking any ScreenshotCard. Stays open until dismissed.

### ScreenshotPanel contents

```
┌─────────────────────────────────┐
│  [×] close                      │
│                                 │
│  ┌─────────────────────────┐    │
│  │                         │    │
│  │   full screenshot PNG   │    │
│  │                         │    │
│  └─────────────────────────┘    │
│                                 │
│  Pastoral · 2026-07-04 · §11   │
│                                 │
│  OCR text                       │
│  ┌─────────────────────────┐    │
│  │ Apple. Now, because...  │    │  ← read-only, selectable
│  └─────────────────────────┘    │
│                                 │
│  Correction (optional)          │
│  ┌─────────────────────────┐    │
│  │                         │    │  ← textarea, PUT on blur
│  └─────────────────────────┘    │
│                                 │
│  Notes                          │
│  ┌─────────────────────────┐    │
│  │                         │    │  ← textarea, PUT on blur
│  └─────────────────────────┘    │
│                                 │
│  ← prev  ·  3 of 25  ·  next → │
└─────────────────────────────────┘
```

**Behaviour:**
- Panel opens instantly with skeleton while image loads
- Image fetched from `GET /api/screenshots/{id}/image`
- OCR text from `ocr_text` field (device original)
- Correction field pre-filled with `ocr_corrected` if set
- Notes field pre-filled with `user_notes` if set
- Both save on blur via `PUT /api/screenshots/{id}`
- Prev/Next navigate through the current book's screenshots without closing panel
- Multiple panels can stack (barnsworthburning trail pattern) — only for screenshots within the same book session

---

## Components

| Component | File | Props |
|-----------|------|-------|
| `BookCard` | `components/BookCard.svelte` | `book: Book` |
| `ScreenshotCard` | `components/ScreenshotCard.svelte` | `screenshot: Screenshot, selected: bool` |
| `ScreenshotPanel` | `components/ScreenshotPanel.svelte` | `id: number, siblings: number[]` |
| `BookIndex` | `components/BookIndex.svelte` | `books: Book[]` |
| `ReadingEntry` | `components/ReadingEntry.svelte` | `entry: ProgressEntry` |
| `AliasRow` | `components/AliasRow.svelte` | `alias: Alias` |
| `StatusWidget` | `components/StatusWidget.svelte` | (fetches own data) |
| `DateGroup` | `components/DateGroup.svelte` | `date: string, screenshots: Screenshot[]` |

---

## State

| Store | Location | Content |
|-------|----------|---------|
| `panelId` | `$state` in layout | Currently open screenshot ID (null = closed) |
| `activeBook` | URL param | Book slug from route |
| `books` | page load data | `Book[]` from `/api/books` |

Panel state lives in a single `$state` variable in `+layout.svelte`. Clicking a card sets it; the panel component reacts and fetches the screenshot. This is simpler than barnsworthburning's full trail system since screenshots don't have arbitrary connections — just prev/next within a book.

---

## Routes

```
src/routes/
  +layout.svelte          ← app shell, nav, sidebar, panel overlay
  +layout.ts              ← load books list for sidebar
  +page.svelte            ← redirect to /books
  books/
    +page.svelte          ← BookCard masonry grid
    +page.ts              ← load all books
    [book]/
      +page.svelte        ← ScreenshotCard grid, grouped by date
      +page.ts            ← load screenshots for book
  log/
    +page.svelte          ← reading log timeline
    +page.ts              ← load reading log
  tbr/
    +page.svelte          ← TBR list
    +page.ts              ← load TBR items
  essays/
    +page.svelte          ← essay download + push to device
    +page.ts              ← load essay sources + queue
  aliases/
    +page.svelte          ← alias management table
    +page.ts              ← load aliases
```

---

## Styling approach (borrowing from barnsworthburning)

- **CSS layers**: `@layer reset, defaults, layout, components`
- **CSS custom properties** for theming: `--sidebar-width: 14rem`, `--panel-width: 32rem`
- **Masonry layout**: `column-width: 36ch` with `break-inside: avoid` (CSS columns, not grid)
- **Color palette**: single neutral palette for now (barnsworthburning's chromatic system not needed — no multi-panel stacking)
- **Dark mode**: `color-scheme: light dark` via `prefers-color-scheme`
- **Typography**: system serif for OCR text (`Georgia, serif`), system sans for UI

---

## TBR List (`/tbr`)

A simple reading queue — books the user wants to read next.

### DB schema (new table: `tbr_books`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `title` | TEXT | Book title |
| `author` | TEXT | Author name |
| `source_url` | TEXT | Optional — Goodreads, Open Library, etc. |
| `notes` | TEXT | Personal notes |
| `added_at` | TIMESTAMP | |
| `status` | TEXT | `'queued'` \| `'reading'` \| `'done'` |

### UI

```
┌──────────────────────────────────────────────────┐
│  To Be Read                          [+ Add book] │
│                                                   │
│  ○  Middlemarch — George Eliot                   │
│  ○  Housekeeping — Marilynne Robinson            │
│  ○  The Rings of Saturn — W.G. Sebald            │
│                                                   │
│  Done (2)  ▸                                     │
└──────────────────────────────────────────────────┘
```

- **Add book**: inline form (title + author + optional URL + notes), saves to DB
- **Status toggle**: click circle → cycle through queued → reading → done
- **Reorder**: drag-and-drop (or up/down buttons)
- **Delete**: hover reveals × button
- **Done section**: collapsed by default, expandable

### API endpoints needed (Phase 9.5)

```
GET  /api/tbr              — list all TBR items
POST /api/tbr              — add item
PUT  /api/tbr/{id}         — update (status, notes, reorder)
DELETE /api/tbr/{id}       — remove
```

---

## Essay Per Day (`/essays`)

Downloads an essay from an online source, converts it to EPUB, and pushes it to the X4 over the Calibre Wireless upload API.

### Flow

```
User clicks "Get Essay"
       │
       ▼
FastAPI fetches essay HTML/text from configured source
       │
       ▼
Convert to EPUB (pandoc or ebooklib)
       │
       ▼
Store in DB (essay_queue table: title, author, url, epub_path, pushed_at)
       │
       ▼
User clicks "Push to X4" (device must be in File Transfer mode)
       │
       ▼
FastAPI uploads EPUB to device via PUT /upload (Calibre Wireless)
       │
       ▼
Show success / move to "Sent" list
```

### Essay sources

Configurable list stored in `ESSAY_SOURCES` env var or DB. Default starting set:

| Source | Notes |
|--------|-------|
| Project Gutenberg short fiction | Free, legal, plain text available |
| Standard Ebooks | High-quality EPUBs, CC0 |
| Aeon Magazine essays | Free to read, long-form |
| The Public Domain Review | Essays on art/history/literature |

### UI

```
┌──────────────────────────────────────────────────┐
│  Essays                                           │
│                                                   │
│  Source: [Standard Ebooks ▾]   [Fetch Essay]     │
│                                                   │
│  ┌────────────────────────────────────────────┐  │
│  │  "The Dead" — James Joyce                  │  │
│  │  Project Gutenberg · fetched 2026-07-05    │  │
│  │  [Push to X4]  [Preview]                   │  │
│  └────────────────────────────────────────────┘  │
│                                                   │
│  Sent to device (3)  ▸                           │
└──────────────────────────────────────────────────┘
```

- **Fetch Essay**: calls `POST /api/essays/fetch` → downloads + converts to EPUB → shows result card
- **Push to X4**: calls `POST /api/essays/{id}/push` → uploads EPUB via Calibre Wireless → device must be online
- **Preview**: opens EPUB metadata / first paragraph in a panel
- **Sent list**: collapsed, shows previously pushed essays

### Conversion pipeline

```
Source HTML/text
       │
       ▼  pandoc (system binary, already in Docker)
       │  or ebooklib (pure Python, no extra dep)
       ▼
EPUB file stored in /data/state/essays/{id}.epub
       │
       ▼
Metadata: title, author, word_count, source_url
```

Prefer `pandoc` if available (already commonly installed). Fall back to `ebooklib`.

### Upload to X4

The Calibre Wireless server exposes file upload. Based on Calibre source, the endpoint is:

```
PUT http://{device}:{port}/upload
Content-Type: multipart/form-data
Body: file={epub_bytes}
```

Needs investigation during File Transfer mode to confirm exact endpoint. Alternative: use the existing `hash_books.py` pattern to confirm device API during a live session.

### DB schema (new table: `essay_queue`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `title` | TEXT | |
| `author` | TEXT | |
| `source_url` | TEXT | Original URL |
| `epub_path` | TEXT | Path to stored EPUB |
| `word_count` | INTEGER | |
| `fetched_at` | TIMESTAMP | |
| `pushed_at` | TIMESTAMP | Null until pushed |
| `push_status` | TEXT | `'pending'` \| `'sent'` \| `'error'` |

### API endpoints needed (Phase 9.5)

```
GET  /api/essays               — list essay queue
POST /api/essays/fetch         — download + convert one essay from source
GET  /api/essays/{id}/preview  — EPUB metadata + first paragraph
POST /api/essays/{id}/push     — upload EPUB to X4 (device must be online)
DELETE /api/essays/{id}        — remove from queue
```
