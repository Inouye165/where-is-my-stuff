# Architecture & Engineering Reference

## 1. Core Design Decisions

- **SQLite WAL + PostgreSQL-compatible SQLAlchemy 2.0:** Every SQLite connection runs `PRAGMA foreign_keys=ON`, `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, and `PRAGMA busy_timeout=5000` (`app/core/database.py`). This gives zero-ops single-file persistence alongside concurrent phone/desktop reads and writes without locking stalls, while keeping all ORM models and parameterized SQL portable to PostgreSQL.
- **Strict Layered Separation:** Routers under `app/api/` validate HTTP inputs and map domain exceptions; `app/repositories/` encapsulates SQLAlchemy queries and recursive CTEs; `app/services/` owns business workflows, image/PDF pipelines, and outbound HTTP calls.
- **Never Hold SQLite Write Locks Across Slow Vision I/O:** Vision endpoints commit uploaded records first, close or exit short-lived sessions (`session_scope(bind)`), execute async `httpx` requests to Ollama/Gemini/OpenAI off-transaction, and reopen a brief session only to persist results.
- **Zero-Build Native ES Modules:** The browser UI runs directly on native `<script type="module">` modules under `app/static/js/` without a Node bundler, keeping local LAN deployment single-command while still allowing syntax checks (`node --check`) and isolated behavioral tests in `tests/`.
- **Dual HTTP + Local HTTPS Binding:** `run.py` binds `0.0.0.0:8000` over HTTP for zero-friction LAN bookmarks and optionally launches `0.0.0.0:8443` with a self-signed local cert (`app/core/tls.py`) because mobile browsers require a secure context for `getUserMedia` camera capture on non-localhost origins.

## 2. Repository Layout

```text
d:\inventory\
├── app\
│   ├── api\                         # 9 versioned FastAPI routers (/api/v1/...)
│   │   ├── auth.py                  # PIN login/logout/status
│   │   ├── documents.py             # Scanner discovery, enhance, OCR, stitching, attachment CRUD/rotate
│   │   ├── items.py                 # Item CRUD, bulk quick-entry, verify, bundle import, box conversion
│   │   ├── label_scan.py            # Phone overview + zone close-up label scanning & grid apply
│   │   ├── locations.py             # Hierarchy tree, grid layout save, slot ensure/move/merge, QR, PDF/photo scan
│   │   ├── photos.py                # Upload, visual crop, vision provider status, phone capture sessions
│   │   ├── search.py                # Keyword search + photo-to-keyword visual search
│   │   ├── system.py                # /health probe (cached disk check), backups list/create/download, orphan crop cleanup
│   │   └── unit_setup.py            # Guided mobile container setup sessions
│   ├── core\                        # Cross-cutting infrastructure (9 modules)
│   │   ├── config.py                # Pydantic Settings + .env loader
│   │   ├── database.py              # Engine, SessionLocal, session_scope, SQLite PRAGMA hook
│   │   ├── exceptions.py            # AppError hierarchy & structured JSON error envelope handlers
│   │   ├── logging_config.py        # Request-ID context filter + rotating logs under logs/
│   │   ├── middleware.py            # Request correlation ID, latency logging, CSP & security headers
│   │   ├── network.py               # LAN IP detection, Host-header/base-URL validation, QR PNG generation
│   │   ├── rate_limit.py            # Per-IP sliding-window rate limits (auth, vision, system, upload)
│   │   ├── security.py              # HMAC session tokens, constant-time PIN verify, LAN/localhost guards
│   │   └── tls.py                   # Self-signed LAN TLS certificate generation & caching
│   ├── db\
│   │   ├── backup.py                # Online SQLite .backup() + PRAGMA integrity_check + retention pruning + scheduler
│   │   └── migrations.py            # Idempotent versioned schema migrations (v1..v10)
│   ├── models\                      # SQLAlchemy 2.0 typed ORM entities (4 files)
│   │   ├── audit.py                 # AuditLog (immutable presence/move log)
│   │   ├── item.py                  # Item + ItemAttachment
│   │   ├── location.py              # Location tree/grid + LocationPhoto gallery
│   │   └── session.py               # CaptureSessionRecord, UnitSetupSessionRecord, LabelScanSessionRecord
│   ├── repositories\                # Parameterized SQLAlchemy data access (2 files)
│   │   ├── item_repository.py       # Filtered/sorted queries, selectinload audits+attachments, escaped ILIKE, ref counts
│   │   └── location_repository.py   # Ordered fetch, grouped item counts, recursive ancestor/descendant CTEs
│   ├── schemas\                     # Pydantic v2 contracts (auth, common, document, item, label_scan, location, search, system, unit_setup)
│   ├── services\                    # Business & media orchestration (17 modules)
│   │   ├── capture_session_service.py
│   │   ├── document_enhance_service.py
│   │   ├── document_ocr_service.py
│   │   ├── document_pdf_service.py
│   │   ├── document_stitch_service.py
│   │   ├── document_workflow_service.py
│   │   ├── image_lookup_service.py
│   │   ├── inventory_service.py
│   │   ├── item_bundle_service.py
│   │   ├── label_scan_service.py
│   │   ├── location_service.py
│   │   ├── photo_service.py
│   │   ├── quick_entry_parser.py
│   │   ├── scanner_service.py
│   │   ├── search_service.py
│   │   ├── unit_setup_service.py
│   │   └── vision_service.py
│   ├── static\                      # Zero-build ES6 SPA & mobile handoff pages
│   │   ├── css\                     # app.css, unit-setup.css
│   │   ├── js\                      # 28 ES modules (see Section 5)
│   │   ├── index.html               # Primary SPA shell
│   │   ├── capture.html             # Mobile QR photo upload page
│   │   ├── setup.html               # Guided mobile container setup page
│   │   ├── label-capture.html       # Mobile label close-up capture page
│   │   ├── manifest.webmanifest
│   │   └── sw.js                    # App-shell offline cache
│   └── main.py                      # FastAPI factory, lifespan migrations/backup scheduler, static mounts
├── data\                            # SQLite DB, uploads/{originals,thumbnails,attachments}, backups/, certs/
├── logs\                            # Rotating server logs
├── tests\                           # Isolated pytest + Node ESM test suite
├── .env.example
├── inventory.ps1                    # Windows PowerShell foreground launcher (-Open, -NoOllama)
├── pyproject.toml                   # Project metadata, ruff & mypy config
├── requirements.txt                 # Pinned runtime & test dependencies
└── run.py                           # Dual HTTP (0.0.0.0:8000) / optional HTTPS (8443) Uvicorn runner
```

## 3. Database Schema Migrations & Query Optimization

`app/db/migrations.py` tracks applied versions in `schema_migrations` and runs all steps transactionally on startup:
1. `001_initial_schema` — creates core `locations`, `items`, `audit_logs` tables and ensures status columns.
2. `002_performance_indexes` — adds lookup indexes on names, categories, parent IDs, and audit timestamps.
3. `003_location_grid_layout` — adds 2D grid dimensions, span coordinates, and `grid_config` JSON.
4. `004_location_photos` — creates `location_photos` gallery table and backfills legacy cover/context photos.
5. `005_item_attachments` — creates `item_attachments` for documents and extra images.
6. `006_location_label` — adds short physical bin `label` column and index on `locations`.
7. `007_grid_slot_unique_index` — enforces unique partial grid coordinates per parent unit (`grid_row`, `grid_col`).
8. `008_persistent_sessions` — persists phone capture and guided unit setup sessions in SQLite tables.
9. `009_label_scan_sessions` — persists auto-label camera sessions with expiry and container indexes.
10. `010_photo_path_indexes` — indexes `items.item_photo`, `locations.container_photo`, `locations.context_photo`, `location_photos.photo_path`, and `item_attachments.file_path` so photo reference counting avoids full table scans.

### Query Efficiency & Caching
- **Hierarchy Snapshot (`LocationHierarchySnapshot`):** Computes full breadcrumbs, inherited photos, and recursive `total_item_count` in `O(N)` memory from at most 2–3 SQL queries (`Location` rows, grouped `Item` counts, and optional `LocationPhoto` rows).
- **Eager Loading & CTEs:** `ItemRepository.get_by_id()` eager-loads both `audit_logs` and `attachments` via `selectinload`. `LocationService.validate_no_cycle()` uses a single recursive CTE (`get_ancestor_ids`), and `LocationService.delete()` fetches descendants via a single CTE (`get_descendant_ids`) and deletes non-container nodes with depth-batched bulk SQL `DELETE` statements instead of ORM relationship cascades.
- **Health Probe Cache (`app/api/system.py`):** Caches successful (`True`) upload-directory write probes per `UPLOAD_DIR` for 10 seconds to avoid unnecessary disk writes under frequent polling, while never caching failures (`False`).

## 4. Photo, Document & Backup Hygiene

- **Bounded Streaming & Image Verification:** `PhotoService` streams uploads in chunks up to `MAX_IMAGE_UPLOAD_SIZE_MB` (15 MB default), verifies decoded headers via Pillow (`MAX_IMAGE_PIXELS = 36MP`), applies EXIF orientation correction, generates WebP thumbnails, writes via `.tmp` + `os.replace()`, and registers SQLAlchemy `after_commit` / `after_rollback` cleanup hooks so orphan files never linger when reference counts hit zero.
- **Document & OCR Pipeline:** OpenCV perspective warp, adaptive enhancement, multi-page image stitching, `rapidocr-onnxruntime` text extraction, and `reportlab` / `pypdf` PDF generation run in worker threads (`anyio.to_thread.run_sync`) with deterministic cleanup of intermediate temporary files.
- **Hot SQLite Backups:** `BackupService` uses `sqlite3.Connection.backup()` + `PRAGMA integrity_check` to archive the live WAL database and uploads without stopping reads or writes.

## 5. Frontend Modules (`app/static/js/`)

All 28 ES modules under `app/static/js/`:
- **Core Shell, Routing & A11y:** `app.js`, `a11y.js` (WCAG 2.2 AA dialog focus trapping & opener restoration), `dom.js` (XSS escaping, URL sanitization, toast ARIA roles, accessible `#app-confirm-modal`), `events.js` (delegated `data-action` dispatcher), `api.js` (timeouts & `BroadcastChannel` sync), `router.js`, `state.js` (cached `containerIdSet`, memoized subtree items, deduplicated `DocumentFragment` dropdown updates), `theme-init.js`, `unsaved-guard.js`.
- **Workspace & Grid Views:** `views.js`, `mobile-views.js`, `grid-designer.js`, `carousel.js`, `icons.js`.
- **Capture, Modals & Workflows:** `modals.js`, `box-actions.js`, `compartment-actions.js`, `document-studio.js`, `item-bundle.js`, `label-scan.js`, `phone-capture.js`, `photo-compress.js`, `photo-editor.js`, `unit-setup-wizard.js`, `vision-guard.js`.
- **Standalone Mobile Handoff Entrypoints:** `capture-page.js` (`capture.html`), `label-capture-page.js` (`label-capture.html`), `mobile-unit-setup.js` (`setup.html`).

## 6. Security & Accessibility Controls

- **Authentication & Rate Limiting:** Optional `WIMS_ACCESS_PIN` with HMAC-signed HTTP-only session cookies, optional localhost bypass, and per-IP sliding-window rate limits covering auth, uploads/bulk creation, system backups, and AI vision/bundle/PDF endpoints.
- **Host-Header, Open Redirect & SSRF Hardening:** `network.py` validates local/LAN `Host` headers and redirect/QR targets; outbound URL/image fetching (`image_lookup_service.py`, `item_bundle_service.py`) disables HTTP redirects, fails closed on DNS failures, blocks private/loopback/link-local/CGNAT IPs both pre-request and on the connected peer socket (`verify_response_peer_is_public`), and bounds response bytes while streaming.
- **HTTP Headers & CSP:** `RequestContextMiddleware` sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, `X-Request-ID`, and strict `Content-Security-Policy` headers (eliminating inline `<script>` blocks).
- **Accessibility (WCAG 2.2 AA):** Skip-to-main navigation link, semantic landmarks, `role="dialog"`/`"alertdialog"` with `aria-modal="true"` and `aria-labelledby` across every modal overlay, Tab/Shift+Tab focus traps, keyboard access on highlight cards, accessible confirmation dialogs replacing blocking `window.confirm()` calls, `:focus-visible` outlines, and `prefers-reduced-motion: reduce` support.
