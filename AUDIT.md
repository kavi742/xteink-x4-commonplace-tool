# Audit — Plan vs. Code Consistency

Reconciles the markdown plan docs (ARCHITECTURE, IMPLEMENTATION, PROJECT_SPEC,
AGENT, CLAUDE, TODO) against the actual code in `xteink_service/`.
The code is the source of truth; stale docs are corrected to match.

Legend: `[ ]` todo · `[x]` done

---

## 1. Critical — supply chain / broken tests

- [x] **1.1 Replace `httpx2` typosquat dependency with real `httpx`**
  - [x] `pyproject.toml`: dev dep `httpx2>=0.28` → `httpx>=0.28`
  - [x] Regenerated `uv.lock` (removed `httpx2` + `httpcore2` + `truststore`; added `httpx 0.28.1` + `httpcore` + `certifi`)
  - [x] `fastapi.testclient` tests now import and pass

## 2. High — docs describe a design the code doesn't implement

- [x] **2.1 Vault layout** `Commonplace/<Book>/attachments/` → `Books/<Title>.md` + `Books/<Title>/<date>-NN.png`
  - [x] `ARCHITECTURE.md` (diagram, archiver steps, data flow)
  - [x] `IMPLEMENTATION.md` (vault_writer snippet)
  - [x] `PROJECT_SPEC.md` (FR2 + Vault Structure)
  - [x] `test_scripts/init-vault.sh`, `test-phase1.sh`, `TESTING.md`, `TODO.md` Phase 1 (dropped unused `Commonplace/`)
- [x] **2.2 Data store §4** `png_data BLOB` / "serve PNG from DB" / `POST /api/vault/export`
      → `vault_png_path` + filesystem serving + `POST /api/vault/rebuild`
  - [x] `ARCHITECTURE.md` §4
  - [x] `TODO.md` Phase 9 schema table
- [x] **2.3 Web UI** "single HTML file / vanilla JS, no build step"
      → SvelteKit built to `web/build`, served at the site root `/`
  - [x] `ARCHITECTURE.md` §4
- [x] **2.4 KOReader schema** `doc_id/page/total_pages`
      → kosync `document/progress/percentage/device/device_id/title/author`
  - [x] `PROJECT_SPEC.md` data model
  - [x] `IMPLEMENTATION.md` model + endpoints + protocol JSON
  - [x] `ARCHITECTURE.md` payload + endpoints
- [x] **2.5 `CLAUDE.md` phase table** — Phases 7–10 marked done; removed stale
      "`python -m xteink_service` currently fails" note

## 3. Medium — contradictions / dead references

- [x] **3.1 `AGENT.md`** guideline #2 "prefer mDNS over polling" → polling (no mDNS)
- [x] **3.2 `PROJECT_SPEC.md`** assumption #3 "Send Document Metadata" → not available in CrossPoint
- [x] **3.3 `IMPLEMENTATION.md`** dead refs `notifications.py` + `requirements.txt`
      → inline `_notify()` + `pyproject.toml`; real module list
- [x] **3.4 `TODO.md`** Phase 5 "Enable Send Document Metadata" struck through with explanation

## 4. Low — code cleanups

- [x] **4.1 `koreader_sync.py`** default `KOREADER_DB` → `/data/state/koreader.db`
      (`ProgressStore` now lazy-inits so import stays filesystem-safe; `conftest.py`
      pins test DB paths to temp files; `alias.py` default aligned too)
- [x] **4.2 `sync_once.py`** default `state_db` → `/data/state/state.db`
- [x] **4.3 `main.py`** removed duplicated `logger = logging.getLogger(__name__)`
- [x] **4.4 `pyproject.toml`** updated stale `[tool.uv] package = false` comment

## 5. Verification

- [x] `uv run pytest` → **84 passed, 1 skipped** (`--live`)
- [x] No stray `Commonplace/`, `doc_id`, `png_data BLOB`, `/api/vault/export`,
      `httpx2`, or `/tmp/koreader.db` left in docs/code (this file excepted)
